"""The job queue port and its two backends.

`arq` in production; an inline backend for development and tests, which runs
the work in the request's own process exactly as `BackgroundTasks` did. The
inline backend is not a stub -- it writes the same job rows and records the
same metrics -- so "how did this job end up failed" is answered the same way
in both, and `make dev` still needs only Postgres and Redis.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from src.jobs.models import Job, JobType
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import jobs_enqueued
from src.monitoring.tracing import get_current_trace_id

logger = get_logger(__name__)


class JobQueue(ABC):
    @abstractmethod
    async def enqueue(
        self, job_type: JobType, document_id: uuid.UUID | None = None, **payload: Any
    ) -> Job: ...

    @abstractmethod
    async def close(self) -> None: ...


class ArqJobQueue(JobQueue):
    """Redis-backed queue.

    A job survives an API restart because the enqueue is a Redis write and
    the worker is a separate process. That is the whole point: CAD and OCR
    work runs for minutes, and an API deploy in the middle of it should cost
    a retry, not a document stuck in `processing` with nothing to notice.
    """

    def __init__(self, redis_settings: Any, job_repo: Any, max_attempts: int = 3) -> None:
        self._redis_settings = redis_settings
        self._job_repo = job_repo
        self._max_attempts = max_attempts
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool

            self._pool = await create_pool(self._redis_settings)
        return self._pool

    async def enqueue(
        self, job_type: JobType, document_id: uuid.UUID | None = None, **payload: Any
    ) -> Job:
        job = Job(
            job_type=job_type,
            document_id=document_id,
            max_attempts=self._max_attempts,
            trace_id=get_current_trace_id(),
            payload=payload,
        )
        # The row is written before the enqueue, not after. A job visible in
        # Redis but absent from the database would be invisible to every
        # status query the UI makes.
        await self._job_repo.save(job)

        pool = await self._get_pool()
        # Only the id crosses the queue -- see worker.run_job. `_job_id`
        # is arq's own deduplication key: a duplicate enqueue of the same job
        # is dropped rather than run twice.
        await pool.enqueue_job("run_job", str(job.id), _job_id=str(job.id))
        jobs_enqueued.labels(job_type=job_type.value, backend="arq").inc()
        logger.info(
            "job_enqueued",
            job_id=str(job.id),
            job_type=job_type.value,
            document_id=str(document_id) if document_id else None,
        )
        return job

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


class InlineJobQueue(JobQueue):
    """Runs the job in this process, awaited by the caller's background task.

    Keeps development and the test suite working with no worker and no extra
    container. It writes the same job rows as arq does, so the observable
    behaviour -- status, attempts, error message -- is identical; what
    differs is only that the work does not survive a restart, which is
    exactly the property production needs arq for.
    """

    def __init__(self, job_repo: Any, runner: Any, max_attempts: int = 1) -> None:
        self._job_repo = job_repo
        self._runner = runner
        self._max_attempts = max_attempts

    async def enqueue(
        self, job_type: JobType, document_id: uuid.UUID | None = None, **payload: Any
    ) -> Job:
        job = Job(
            job_type=job_type,
            document_id=document_id,
            # One attempt: there is no worker to retry into, and retrying in
            # the request's own process would just repeat the same failure
            # while holding the connection.
            max_attempts=self._max_attempts,
            trace_id=get_current_trace_id(),
            payload=payload,
        )
        await self._job_repo.save(job)
        jobs_enqueued.labels(job_type=job_type.value, backend="inline").inc()
        await self._runner(job)
        return job

    async def close(self) -> None:
        return None
