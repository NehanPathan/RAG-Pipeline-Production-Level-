"""arq worker entrypoint.

Run with `arq src.jobs.worker.WorkerSettings`, or via the `worker` service in
docker-compose.

A separate process from the API on purpose. Ingestion of a large drawing set
is minutes of CPU-bound work -- Docling layout inference, OCR, CAD parsing,
embedding -- and running it inside the API meant one upload could starve
every other request on a single-worker uvicorn. It also meant an API deploy
mid-ingestion silently abandoned the document.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from src.config import get_settings
from src.monitoring.logger import configure_logging, get_logger
from src.monitoring.tracing import configure_tracing

logger = get_logger(__name__)


async def run_job(ctx: dict[str, Any], job_id: str) -> str:
    """Dispatch one job by id.

    Only the id crosses the queue. The job's own row is the source of truth
    for its type, payload and attempt count, so a worker running older code
    cannot act on a payload shape it does not understand -- and a retry
    always re-reads current state rather than replaying a stale copy.
    """
    from src.api.dependencies import get_job_repository, get_job_runner

    repo = get_job_repository()
    job = await repo.get_by_id(uuid.UUID(job_id))
    if job is None:
        # The document was deleted before the worker got to it. Not an error
        # worth retrying.
        logger.warning("job_not_found", job_id=job_id)
        return "missing"

    await get_job_runner().run(job)
    return job.status.value


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.otel_exporter_otlp_endpoint:
        configure_tracing(f"{settings.otel_service_name}-worker", settings.otel_exporter_otlp_endpoint)

    # Load the embedding and reranking models now rather than on the first
    # job. They are hundreds of megabytes and the download would otherwise
    # count against that job's timeout.
    import asyncio

    from src.api.dependencies import get_ingestion_pipeline

    await asyncio.to_thread(get_ingestion_pipeline)
    logger.info("worker_started", concurrency=settings.job_worker_concurrency)


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker_stopped")


def _redis_settings() -> Any:
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """arq configuration."""

    # One dispatch function, not one per job type: only the job id crosses
    # the queue, so adding a job type is a branch in JobRunner rather than a
    # new registration the worker must be redeployed to learn.
    functions: ClassVar[list[Any]] = [run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().job_worker_concurrency
    job_timeout = get_settings().job_timeout_seconds
    max_tries = get_settings().job_max_attempts
    # Keep finished jobs in Redis briefly for arq's own inspection; the
    # durable record is the `jobs` table, not this.
    keep_result = 3600
