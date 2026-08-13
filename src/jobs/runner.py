"""Executes a job, whichever backend dispatched it.

One implementation for both, so retry accounting, status transitions,
metrics and the idempotency rule cannot drift between production and
development.

Idempotency is the part worth stating plainly. Re-running ingestion for a
document must not double its chunks: a retry after a partial failure, or an
operator hitting reprocess, would otherwise leave two copies of every
passage in Qdrant and Elasticsearch. Both would be retrieved, both would be
cited, and the duplicate would look like corroboration. So ingestion deletes
what it previously wrote for that document before writing again.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from src.jobs.models import Job, JobStatus, JobType
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import job_duration, jobs_processed
from src.monitoring.tracing import set_current_trace_id

logger = get_logger(__name__)


class JobRunner:
    def __init__(
        self,
        job_repo: Any,
        document_repo: Any,
        ingestion_pipeline_factory: Any,
        chunk_repo: Any = None,
        vector_repo: Any = None,
        search_repo: Any = None,
        blob_store: Any = None,
    ) -> None:
        self._jobs = job_repo
        self._documents = document_repo
        self._pipeline_factory = ingestion_pipeline_factory
        self._chunks = chunk_repo
        self._vector = vector_repo
        self._search = search_repo
        self._blobs = blob_store

    async def run(self, job: Job) -> Job:
        # The trace id travels with the job, so a failure minutes after the
        # upload still correlates to the request that caused it.
        if job.trace_id:
            set_current_trace_id(job.trace_id)
            structlog.contextvars.bind_contextvars(trace_id=job.trace_id)

        job.mark_running()
        await self._jobs.save(job)
        started = time.perf_counter()

        try:
            if job.job_type is JobType.INGEST_DOCUMENT or job.job_type is JobType.REINDEX_DOCUMENT:
                await self._ingest(job)
            else:
                raise ValueError(f"No handler for job type {job.job_type!r}")
        except Exception as exc:
            job.mark_failed(str(exc))
            await self._jobs.save(job)
            jobs_processed.labels(
                job_type=job.job_type.value,
                outcome="failed" if job.is_exhausted else "retrying",
            ).inc()
            logger.error(
                "job_failed",
                job_id=str(job.id),
                job_type=job.job_type.value,
                attempt=job.attempts,
                exhausted=job.is_exhausted,
                error=str(exc),
            )
            # Re-raised so arq applies its own backoff and retry. Swallowing
            # it here would mark the job failed and then tell the worker it
            # succeeded, which is how a queue quietly stops retrying.
            raise
        else:
            job.mark_succeeded()
            await self._jobs.save(job)
            jobs_processed.labels(job_type=job.job_type.value, outcome="succeeded").inc()
        finally:
            job_duration.labels(job_type=job.job_type.value).observe(
                time.perf_counter() - started
            )

        logger.info(
            "job_succeeded",
            job_id=str(job.id),
            job_type=job.job_type.value,
            duration_s=round(job.duration_seconds or 0.0, 2),
        )
        return job

    async def _ingest(self, job: Job) -> None:
        if job.document_id is None:
            raise ValueError("Ingestion job carries no document id.")

        document = await self._documents.get_by_id(job.document_id)
        if document is None:
            raise ValueError(f"Document {job.document_id} no longer exists.")

        await self._clear_previous_output(job.document_id)

        file_path = await self._materialise(document, job)
        pipeline = self._pipeline_factory()
        try:
            await pipeline.ingest(document, file_path)
        finally:
            # The blob is the durable copy; this was only a working file for
            # the parsers, which need a path rather than a stream.
            if file_path is not None and job.payload.get("cleanup_file", True):
                file_path.unlink(missing_ok=True)

    async def _clear_previous_output(self, document_id: uuid.UUID) -> None:
        """Remove anything a previous attempt wrote for this document.

        Without this, a retry doubles every chunk: both copies are retrieved,
        both are cited, and the duplicate reads as corroboration.
        """
        for store, name in (
            (self._vector, "qdrant"),
            (self._search, "elasticsearch"),
            (self._chunks, "postgres"),
        ):
            if store is None:
                continue
            try:
                await store.delete_by_document(document_id)
            except Exception as exc:
                # A store that has never seen this document is the normal
                # case on a first attempt.
                logger.debug(
                    "job_cleanup_skipped", store=name, document_id=str(document_id), error=str(exc)
                )

    async def _materialise(self, document: Any, job: Job) -> Any:
        """Write the stored blob to a local path the parsers can open.

        Docling, ezdxf, pdf2image and Tesseract all take filenames. The
        worker is a different process from the API, so it cannot rely on a
        file the upload request happened to leave behind.
        """
        from pathlib import Path

        local = job.payload.get("file_path")
        if isinstance(local, str) and local:
            path = Path(local)
            if path.exists():
                return path

        if self._blobs is None or not document.file_path:
            raise ValueError(
                f"Document {document.id} has no readable source: no local file and no blob key."
            )

        from src.config import get_settings

        work_dir = Path(get_settings().derived_assets_dir) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / f"{document.id}_{document.file_name}"
        with path.open("wb") as handle:
            async for chunk in self._blobs.get_stream(document.file_path):
                handle.write(chunk)
        return path


def is_terminal(job: Job) -> bool:
    return job.status in (JobStatus.SUCCEEDED, JobStatus.SKIPPED) or job.is_exhausted
