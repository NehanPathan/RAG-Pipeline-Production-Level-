from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.infrastructure.database.postgres.models import EvaluationMetricModel, EvaluationRunModel


async def create_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    name: str,
    dataset_name: str,
    model_config: dict,
    retrieval_config: dict,
    total_items: int,
) -> EvaluationRunModel:
    run = EvaluationRunModel(
        id=run_id,
        name=name,
        dataset_name=dataset_name,
        triggered_by="manual",
        model_config_data=model_config,
        retrieval_config=retrieval_config,
        status="running",
        total_items=total_items,
        completed_items=0,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def update_run_progress(
    session: AsyncSession, run_id: uuid.UUID, completed: int
) -> None:
    run = await session.get(EvaluationRunModel, run_id)
    if run:
        run.completed_items = completed
        await session.commit()


async def finish_run(
    session: AsyncSession, run_id: uuid.UUID, *, error: str | None = None
) -> None:
    run = await session.get(EvaluationRunModel, run_id)
    if run:
        run.status = "failed" if error else "completed"
        run.completed_at = datetime.now(UTC)
        if error:
            run.error_message = error
        await session.commit()


async def save_metrics(
    session: AsyncSession,
    run_id: uuid.UUID,
    metrics: dict[str, float],
    sample_size: int,
) -> None:
    for name, value in metrics.items():
        m = EvaluationMetricModel(
            run_id=run_id,
            metric_name=name,
            value=value,
            aggregation="mean",
            sample_size=sample_size,
        )
        session.add(m)
    await session.commit()


async def list_runs(session: AsyncSession) -> list[EvaluationRunModel]:
    result = await session.execute(
        select(EvaluationRunModel)
        .options(selectinload(EvaluationRunModel.metrics))
        .order_by(EvaluationRunModel.started_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> EvaluationRunModel | None:
    result = await session.execute(
        select(EvaluationRunModel)
        .options(selectinload(EvaluationRunModel.metrics))
        .where(EvaluationRunModel.id == run_id)
    )
    return result.scalar_one_or_none()
