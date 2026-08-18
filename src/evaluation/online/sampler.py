from __future__ import annotations

import hashlib
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.evaluation.offline.metrics import (
    score_answer_relevancy,
    score_context_relevancy,
    score_faithfulness,
)
from src.evaluation.online.repository import save_sample
from src.governance.policy import get_policy
from src.governance.runtime_flags import get_flags
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import online_eval_sampled, online_eval_score
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)


def should_sample(trace_id: str, sample_rate: float) -> bool:
    """Deterministic sampling decision keyed on the trace id.

    Hashing rather than `random()` buys three things worth more than the
    simplicity it costs: the same request always makes the same decision, so
    a re-run reproduces it; the sample is uncorrelated with time of day, so a
    quiet night does not skew coverage; and the decision is testable without
    monkeypatching a random source.
    """
    if sample_rate <= 0:
        return False
    if sample_rate >= 1:
        return True
    if not trace_id:
        return False
    bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) % 10_000
    return bucket < int(sample_rate * 10_000)


class OnlineEvaluator:
    """Scores a sample of live answers with the offline judge.

    Reuses `evaluation.offline.metrics` deliberately. Writing a second,
    "lighter" set of judge prompts for production would produce numbers on a
    different scale from the offline dashboard, and the first time the two
    disagreed nobody would know which to believe. Same prompts, same model
    role, same 0-1 range — so an online faithfulness of 0.62 and an offline
    faithfulness of 0.81 is a fact about the traffic, not about the metric.
    """

    def __init__(
        self,
        scorer_llm: LLMProvider,
        session_factory: async_sessionmaker[AsyncSession],
        sample_rate: float | None = None,
        min_per_hour: int | None = None,
    ) -> None:
        self._llm = scorer_llm
        self._session_factory = session_factory
        self._sample_rate = (
            sample_rate if sample_rate is not None else get_policy().online_eval_sample_rate
        )
        from src.config import get_settings

        self._min_per_hour = (
            min_per_hour
            if min_per_hour is not None
            else get_settings().governance_online_eval_min_per_hour
        )
        self._window_started = time.time()
        self._window_count = 0

    async def maybe_score(
        self,
        *,
        trace_id: str,
        question: str,
        answer: str,
        contexts: list[str],
        citation_count: int,
        message_id: uuid.UUID | None = None,
    ) -> dict[str, float] | None:
        """Score this interaction if it falls in the sample; else no-op.

        Runs as a background task from the chat route, so a slow judge delays
        nothing the user is waiting on. Every failure path is swallowed and
        counted: evaluation is an observer of the system, and an observer
        that can break the thing it observes is a liability.
        """
        flags = await get_flags()
        if not flags.online_eval_enabled:
            online_eval_sampled.labels(result="disabled").inc()
            return None

        if not should_sample(trace_id, self._sample_rate) and not self._below_floor():
            online_eval_sampled.labels(result="skipped").inc()
            return None

        if not answer or not contexts:
            # Refusals and empty retrievals are already counted by
            # `rag_answers_total{outcome="refused"}`; judging them would
            # score the guardrail rather than the model.
            online_eval_sampled.labels(result="skipped").inc()
            return None

        try:
            async with traced_stage(
                "online_evaluation", contexts=len(contexts), scorer=self._llm.model_id
            ) as stage:
                scores = {
                    "faithfulness": await score_faithfulness(self._llm, answer, contexts),
                    "answer_relevancy": await score_answer_relevancy(
                        self._llm, question, answer
                    ),
                    "context_relevancy": await score_context_relevancy(
                        self._llm, question, contexts
                    ),
                }
                stage.set_result(**{k: round(v, 3) for k, v in scores.items()})
        except Exception as exc:
            online_eval_sampled.labels(result="failed").inc()
            logger.warning("online_eval_failed", trace_id=trace_id, error=str(exc))
            await self._persist(
                trace_id, message_id, question, answer, contexts, citation_count,
                {}, status="failed", error_message=str(exc),
            )
            return None

        for name, value in scores.items():
            online_eval_score.labels(metric=name).observe(value)
        online_eval_sampled.labels(result="scored").inc()

        await self._persist(
            trace_id, message_id, question, answer, contexts, citation_count, scores
        )
        self._warn_on_floor_breach(scores, trace_id)
        return scores

    def _below_floor(self) -> bool:
        """Whether to sample regardless of the rate, to guarantee a minimum.

        Percentage sampling produces nothing on low traffic: 5% of a few dozen
        queries a day rounds to zero, so the online dashboard stays empty and
        the drift alerts never have data to fire on — the metric looks healthy
        because it does not exist. This floor guarantees a trickle of samples
        whatever the volume.

        Counted in-process rather than queried per request. The counter resets
        on restart, which slightly over-samples after a deploy; that is the
        right direction to be wrong in, and it costs no database round-trip on
        the hot path.
        """
        if self._min_per_hour <= 0:
            return False

        now = time.time()
        if now - self._window_started >= 3600:
            self._window_started = now
            self._window_count = 0

        if self._window_count < self._min_per_hour:
            self._window_count += 1
            online_eval_sampled.labels(result="floor").inc()
            return True
        return False

    def _warn_on_floor_breach(self, scores: dict[str, float], trace_id: str) -> None:
        """Log any individual sample that lands under a policy floor.

        The Prometheus alert fires on the *rolling average*, which is the
        right signal for paging someone. This is the complementary one: a
        single bad answer with a trace id attached, which is what an engineer
        actually needs to debug the cause.
        """
        policy = get_policy()
        for name, value in scores.items():
            decision = policy.check_quality(name, value)
            if decision.denied:
                logger.warning(
                    "online_eval_below_policy_floor",
                    metric=name,
                    value=round(value, 3),
                    floor=policy.quality_floor(name),
                    trace_id=trace_id,
                )

    async def _persist(
        self,
        trace_id: str,
        message_id: uuid.UUID | None,
        question: str,
        answer: str,
        contexts: list[str],
        citation_count: int,
        scores: dict[str, float],
        status: str = "scored",
        error_message: str | None = None,
    ) -> None:
        try:
            await save_sample(
                self._session_factory,
                trace_id=trace_id,
                message_id=message_id,
                question=question,
                answer=answer,
                context_count=len(contexts),
                citation_count=citation_count,
                scores=scores,
                scorer_model=self._llm.model_id,
                status=status,
                error_message=error_message,
            )
        except Exception as exc:
            logger.warning("online_eval_persist_failed", trace_id=trace_id, error=str(exc))
