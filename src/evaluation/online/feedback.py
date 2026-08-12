from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.repositories.conversation_repository import ConversationRepository
from src.evaluation.online.repository import (
    mark_promoted,
    save_feedback,
    unpromoted_negative_feedback,
)
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import user_feedback

logger = get_logger(__name__)

_DATASET_DIR = Path(__file__).parents[3] / "data" / "eval_datasets"

# Tags a user can attach to a rating. Closed set so the metric label stays
# bounded -- free-text tags would create unbounded Prometheus cardinality,
# the classic way to take down a metrics backend with a feedback form.
ALLOWED_TAGS = frozenset(
    {
        "hallucinated",
        "wrong_source",
        "incomplete",
        "outdated",
        "irrelevant",
        "too_slow",
        "helpful",
        "other",
    }
)

NEGATIVE_RATING_MAX = 2


class FeedbackService:
    """Records explicit user feedback and closes the loop back into evaluation.

    Feedback is the only signal in the system that reflects whether an answer
    was actually *useful*, as opposed to grounded, relevant, or fast — the
    things an LLM judge can assess. It is therefore the input the golden
    dataset is least able to generate for itself, which is why negative
    feedback is promoted into that dataset rather than merely counted.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        conversation_repo: ConversationRepository,
    ) -> None:
        self._session_factory = session_factory
        self._conversations = conversation_repo

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        rating: int,
        message_id: uuid.UUID | None = None,
        trace_id: str = "",
        comment: str | None = None,
        tags: list[str] | None = None,
    ) -> uuid.UUID:
        clean_tags = sorted({t for t in (tags or []) if t in ALLOWED_TAGS})
        sentiment = "negative" if rating <= NEGATIVE_RATING_MAX else "positive"

        feedback_id = await save_feedback(
            self._session_factory,
            user_id=user_id,
            message_id=message_id,
            trace_id=trace_id,
            rating=rating,
            comment=comment,
            tags=clean_tags,
        )

        # One series per tag, plus an untagged series, so "how much negative
        # feedback" and "how much of it was hallucination" are both answerable
        # without post-processing.
        for tag in clean_tags or ["untagged"]:
            user_feedback.labels(sentiment=sentiment, tag=tag).inc()

        await audit_record(
            action=AuditAction.FEEDBACK_SUBMITTED,
            actor_id=user_id,
            resource_type="message",
            resource_id=str(message_id) if message_id else trace_id,
            outcome=AuditOutcome.COMPLETED,
            reason=f"rating={rating} tags={clean_tags}",
        )
        logger.info(
            "feedback_recorded",
            feedback_id=str(feedback_id),
            rating=rating,
            tags=clean_tags,
            trace_id=trace_id,
        )
        return feedback_id

    async def promote_negatives_to_dataset(
        self, dataset_name: str = "regression_from_feedback", limit: int = 100
    ) -> dict:
        """Fold un-promoted negative feedback into a golden dataset file.

        This is the MANAGE function's closing move: a question a user marked
        as badly answered becomes a permanent regression case, so the next
        offline run — and the CI quality gate — measures whether the fix
        actually held. Without this step, feedback is a dashboard nobody acts
        on.

        Idempotent via `promoted_to_dataset`; safe to run on a schedule.
        """
        pending = await unpromoted_negative_feedback(self._session_factory, limit=limit)
        if not pending:
            return {"added": 0, "skipped": 0, "dataset": dataset_name}

        path = _DATASET_DIR / f"{dataset_name}.json"
        existing = self._load_existing(path)
        known_questions = {item.get("question", "") for item in existing}

        added = 0
        skipped = 0
        promoted_ids: list[uuid.UUID] = []

        for feedback in pending:
            promoted_ids.append(feedback.id)
            question = None
            if feedback.message_id:
                question = await self._conversations.get_question_for_answer(
                    feedback.message_id
                )
            if not question or question in known_questions:
                # Still marked promoted: the row has been considered and
                # re-examining it on every run would make this job's cost
                # grow forever without ever producing a different outcome.
                skipped += 1
                continue

            existing.append(
                {
                    "question": question,
                    "ground_truth": None,
                    "notes": (
                        f"Auto-promoted from user feedback {feedback.id} "
                        f"(rating={feedback.rating}, tags={list(feedback.feedback_tags or [])})."
                    ),
                }
            )
            known_questions.add(question)
            added += 1

        if added:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

        await mark_promoted(self._session_factory, promoted_ids)
        logger.info(
            "feedback_promoted_to_dataset",
            dataset=dataset_name,
            added=added,
            skipped=skipped,
        )
        return {"added": added, "skipped": skipped, "dataset": dataset_name}

    @staticmethod
    def _load_existing(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            # Refuse to silently overwrite a file we cannot parse -- that
            # would destroy a hand-curated dataset on one bad character.
            logger.error("eval_dataset_unparseable", path=str(path))
            raise
