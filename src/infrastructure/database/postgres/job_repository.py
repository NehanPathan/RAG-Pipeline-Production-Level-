from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.postgres.models import JobModel
from src.jobs.models import Job, JobStatus, JobType


class PostgresJobRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, job: Job) -> Job:
        """Insert or update, in one statement.

        Upsert rather than insert-then-update because `save` is called at
        every transition -- queued, running, succeeded or failed -- and the
        caller should not have to know which of those is the first.
        """
        async with self._session_factory() as session:
            await session.execute(
                pg_insert(JobModel)
                .values(
                    id=job.id,
                    job_type=job.job_type.value,
                    status=job.status.value,
                    document_id=job.document_id,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    error_message=job.error_message,
                    trace_id=job.trace_id or None,
                    payload=job.payload,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "status": job.status.value,
                        "attempts": job.attempts,
                        "error_message": job.error_message,
                        "started_at": job.started_at,
                        "finished_at": job.finished_at,
                    },
                )
            )
            await session.commit()
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        async with self._session_factory() as session:
            model = await session.get(JobModel, job_id)
            return _to_entity(model) if model else None

    async def list_for_document(self, document_id: uuid.UUID) -> list[Job]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(JobModel)
                .where(JobModel.document_id == document_id)
                .order_by(JobModel.created_at.desc())
            )
            return [_to_entity(m) for m in result.scalars().all()]

    async def has_active_job(self, document_id: uuid.UUID) -> bool:
        """Whether this document already has work in flight.

        The idempotency guard on reprocess: enqueuing a second ingestion for
        a document already being ingested would have two workers writing the
        same chunks, and the loser's partial output would survive as
        duplicates.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(JobModel.id).where(
                    JobModel.document_id == document_id,
                    JobModel.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
                )
            )
            return result.first() is not None

    async def count_stuck(self, older_than_seconds: int = 3600) -> int:
        """Jobs claiming to be running long past any plausible duration.

        A worker killed mid-job leaves its row `running` forever -- nothing
        transitions it, because the thing that would have is gone. Counting
        them is what turns a silently stalled queue into an alert.
        """
        cutoff = datetime.utcnow() - timedelta(seconds=older_than_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                select(JobModel.id).where(
                    JobModel.status == JobStatus.RUNNING.value,
                    JobModel.started_at < cutoff,
                )
            )
            return len(list(result.scalars().all()))


def _to_entity(model: JobModel) -> Job:
    return Job(
        id=model.id,
        job_type=JobType(model.job_type),
        status=JobStatus(model.status),
        document_id=model.document_id,
        attempts=model.attempts,
        max_attempts=model.max_attempts,
        error_message=model.error_message,
        trace_id=model.trace_id or "",
        payload=dict(model.payload or {}),
        created_at=model.created_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
    )
