from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.postgres.models import (
    OnlineEvalSampleModel,
    UserFeedbackModel,
)


async def save_sample(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trace_id: str,
    message_id: uuid.UUID | None,
    question: str,
    answer: str,
    context_count: int,
    citation_count: int,
    scores: dict[str, float],
    scorer_model: str,
    status: str = "scored",
    error_message: str | None = None,
) -> uuid.UUID:
    sample_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            OnlineEvalSampleModel(
                id=sample_id,
                trace_id=trace_id or None,
                message_id=message_id,
                question=question[:8000],
                answer=answer[:16000],
                context_count=context_count,
                citation_count=citation_count,
                scores=scores,
                scorer_model=scorer_model,
                status=status,
                error_message=error_message,
            )
        )
        await session.commit()
    return sample_id


async def recent_samples(
    session_factory: async_sessionmaker[AsyncSession], limit: int = 50
) -> list[OnlineEvalSampleModel]:
    async with session_factory() as session:
        result = await session.execute(
            select(OnlineEvalSampleModel)
            .order_by(OnlineEvalSampleModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def rolling_averages(
    session_factory: async_sessionmaker[AsyncSession], window_hours: int = 24
) -> dict[str, float]:
    """Mean of each judged metric over a trailing window.

    Averaged in Python rather than SQL because the scores live in a JSONB
    blob whose key set is intentionally open — adding a new judge metric
    should not require a schema change or a new query.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    async with session_factory() as session:
        result = await session.execute(
            select(OnlineEvalSampleModel.scores).where(
                OnlineEvalSampleModel.created_at >= cutoff,
                OnlineEvalSampleModel.status == "scored",
            )
        )
        rows = [r[0] or {} for r in result.all()]

    totals: dict[str, list[float]] = {}
    for scores in rows:
        for name, value in scores.items():
            if isinstance(value, (int, float)):
                totals.setdefault(name, []).append(float(value))
    return {name: sum(vals) / len(vals) for name, vals in totals.items() if vals}


async def save_feedback(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    message_id: uuid.UUID | None,
    trace_id: str,
    rating: int,
    comment: str | None,
    tags: list[str] | None,
) -> uuid.UUID:
    feedback_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            UserFeedbackModel(
                id=feedback_id,
                user_id=user_id,
                message_id=message_id,
                trace_id=trace_id or None,
                rating=rating,
                comment=comment,
                feedback_tags=tags or [],
            )
        )
        await session.commit()
    return feedback_id


async def feedback_summary(
    session_factory: async_sessionmaker[AsyncSession], window_hours: int = 168
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    async with session_factory() as session:
        result = await session.execute(
            select(
                func.count(UserFeedbackModel.id),
                func.avg(UserFeedbackModel.rating),
                func.count(UserFeedbackModel.id).filter(UserFeedbackModel.rating <= 2),
            ).where(UserFeedbackModel.created_at >= cutoff)
        )
        total, average, negative = result.one()

    return {
        "window_hours": window_hours,
        "total": int(total or 0),
        "average_rating": round(float(average), 3) if average is not None else None,
        "negative": int(negative or 0),
        "negative_rate": round(float(negative) / float(total), 3) if total else 0.0,
    }


async def unpromoted_negative_feedback(
    session_factory: async_sessionmaker[AsyncSession], limit: int = 100
) -> list[UserFeedbackModel]:
    """Negative feedback not yet folded into the golden dataset.

    The `promoted_to_dataset` flag is what keeps the MANAGE feedback loop
    idempotent: without it, every run of the promotion job would re-add the
    same complaints and the golden set would grow without bound.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(UserFeedbackModel)
            .where(
                UserFeedbackModel.rating <= 2,
                UserFeedbackModel.promoted_to_dataset.is_(False),
            )
            .order_by(UserFeedbackModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def mark_promoted(
    session_factory: async_sessionmaker[AsyncSession], feedback_ids: list[uuid.UUID]
) -> None:
    if not feedback_ids:
        return
    async with session_factory() as session:
        await session.execute(
            update(UserFeedbackModel)
            .where(UserFeedbackModel.id.in_(feedback_ids))
            .values(promoted_to_dataset=True)
        )
        await session.commit()
