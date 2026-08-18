from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.evaluation.offline.dataset import EvalDataset
from src.evaluation.offline.metrics import (
    score_answer_correctness,
    score_answer_relevancy,
    score_context_relevancy,
    score_faithfulness,
)
from src.evaluation.offline.repository import (
    create_run,
    finish_run,
    save_metrics,
    update_run_progress,
)
from src.governance.policy import get_policy
from src.governance.rbac import system_principal
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import evaluation_score
from src.retrieval.pipeline import QueryPipeline

logger = get_logger(__name__)


async def run_evaluation(
    *,
    run_id: uuid.UUID,
    run_name: str,
    dataset: EvalDataset,
    pipeline: QueryPipeline,
    scorer_llm: LLMProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Background task: runs every sample through the RAG pipeline, scores
    each one with the LLM-based metrics, then writes aggregated results to DB.

    Two governance properties this run guarantees:

      Reproducibility — the retrieval configuration recorded against the run
      is read from the live pipeline, not hard-coded. A stored config that
      does not match the one that ran makes every comparison between runs
      meaningless, which is worse than storing nothing.

      Full corpus coverage — evaluation retrieves as a system principal, so
      classification does not silently shrink the corpus being measured.
      Scores would otherwise describe whatever subset the default clearance
      happened to allow.
    """
    policy = get_policy()
    evaluator = system_principal()
    retrieval_config = pipeline.describe_config()

    async with session_factory() as session:
        await create_run(
            session,
            run_id=run_id,
            name=run_name,
            dataset_name=dataset.name,
            model_config={
                "scorer": scorer_llm.model_id,
                "generator": retrieval_config.get("generator_model", "unknown"),
                "policy_version": policy.version,
            },
            retrieval_config=retrieval_config,
            total_items=len(dataset),
        )

    scores: dict[str, list[float]] = defaultdict(list)
    skipped = 0

    for i, sample in enumerate(dataset.samples):
        logger.info("eval_sample_start", run_id=str(run_id), sample=i + 1, total=len(dataset))
        try:
            # Run retrieval to get contexts
            inspection = await pipeline.inspect(sample.question, principal=evaluator)
            contexts = [r.chunk.content for r in inspection.reranked_results[:5]]

            # Run full pipeline to get the generated answer
            answer = ""
            async for event in pipeline.answer(sample.question, principal=evaluator):
                if event["type"] == "done":
                    answer = "" if event.get("refused") else event["answer"]
                    break
                elif event["type"] == "error":
                    answer = ""
                    break

            if not answer:
                # Covers both generation failures and policy refusals. A
                # refusal is correct behaviour, not a quality signal -- scoring
                # it would measure the guardrail rather than the model.
                logger.warning("eval_sample_no_answer", sample=i + 1)
                skipped += 1
                continue

            # None means the judge failed or returned something unparseable;
            # those are excluded from the mean rather than counted as zero.
            sample_scores = {
                "faithfulness": await score_faithfulness(scorer_llm, answer, contexts),
                "answer_relevancy": await score_answer_relevancy(
                    scorer_llm, sample.question, answer
                ),
                "context_relevancy": await score_context_relevancy(
                    scorer_llm, sample.question, contexts
                ),
            }
            if sample.ground_truth:
                sample_scores["answer_correctness"] = await score_answer_correctness(
                    scorer_llm, sample.question, answer, sample.ground_truth
                )

            for name, value in sample_scores.items():
                if value is not None:
                    scores[name].append(value)

            logger.info(
                "eval_sample_done",
                sample=i + 1,
                **{
                    k: (f"{v:.2f}" if v is not None else "unscored")
                    for k, v in sample_scores.items()
                },
            )

        except Exception as exc:
            logger.error("eval_sample_failed", sample=i + 1, error=str(exc))
            skipped += 1

        async with session_factory() as session:
            await update_run_progress(session, run_id, completed=i + 1)

    if not scores:
        async with session_factory() as session:
            await finish_run(session, run_id, error="No samples scored successfully.")
        return

    aggregated = {k: sum(v) / len(v) for k, v in scores.items()}
    sample_size = max(len(v) for v in scores.values())

    # Publish to Prometheus so quality sits on the same dashboard, and under
    # the same alerting rules, as latency and error rate. Quality that only
    # exists in a database table is quality nobody is paged about.
    for name, value in aggregated.items():
        evaluation_score.labels(metric=name, dataset=dataset.name).set(value)

    breaches = [
        f"{name}={value:.3f} < {policy.quality_floor(name)}"
        for name, value in aggregated.items()
        if policy.check_quality(name, value).denied
    ]
    if breaches:
        logger.warning(
            "eval_run_below_policy_floor",
            run_id=str(run_id),
            breaches=breaches,
            policy_version=policy.version,
        )

    async with session_factory() as session:
        await save_metrics(session, run_id, aggregated, sample_size)
        await finish_run(session, run_id)

    logger.info(
        "eval_run_complete",
        run_id=str(run_id),
        metrics=aggregated,
        skipped=skipped,
        breaches=breaches,
    )
