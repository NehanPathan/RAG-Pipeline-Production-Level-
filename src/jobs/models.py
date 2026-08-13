"""Job records.

Ingestion previously ran in a FastAPI `BackgroundTask`: in-process, no
retry, no persistence, and dying with the worker. A document whose ingestion
was interrupted sat in `processing` forever, with nothing to retry it and
nothing recording why it stopped -- and CAD and OCR work is minutes-long, so
the window for that is wide rather than theoretical.

A job row exists so the answer to "what happened to my upload" is a query
rather than a guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Distinct from FAILED: the work was not attempted because an identical
    # job was already in flight. Not an error, and not something to alert on.
    SKIPPED = "skipped"


class JobType(str, Enum):
    INGEST_DOCUMENT = "ingest_document"
    REINDEX_DOCUMENT = "reindex_document"


@dataclass
class Job:
    job_type: JobType
    document_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    error_message: str | None = None
    # Set from the request that enqueued the job, so a failure days later is
    # still traceable to the upload that caused it.
    trace_id: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.attempts += 1
        self.started_at = datetime.utcnow()

    def mark_succeeded(self) -> None:
        self.status = JobStatus.SUCCEEDED
        self.finished_at = datetime.utcnow()

    def mark_failed(self, error: str) -> None:
        """Record a failure, keeping the job retryable until attempts run out.

        The distinction matters for alerting: a job that will be retried is
        not yet a problem, and a job that has exhausted its attempts is one
        that needs a human.
        """
        self.error_message = error[:2000]
        self.finished_at = datetime.utcnow()
        self.status = JobStatus.FAILED

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
