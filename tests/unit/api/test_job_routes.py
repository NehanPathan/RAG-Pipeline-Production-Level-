"""The ingestion queue.

`GET /jobs` did not exist, so the queue page had nothing to call -- the one
view whose entire purpose is answering "why has my upload not finished".

A job row carries no classification of its own, so its scope is the
ownership of the document it refers to. That is the whole of the access
model here, and it is what these tests are mostly about.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api import dependencies_rate_limit
from src.api.main import app
from src.api.routes import documents as document_routes
from src.domain.value_objects.sensitivity import Sensitivity
from src.jobs.models import Job, JobStatus, JobType

ME = uuid.uuid4()


class _FakeJobRepository:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.calls: list[dict] = []

    async def list_recent(self, statuses=None, limit=50, user_id=None, all_documents=False):
        self.calls.append(
            {
                "statuses": statuses,
                "limit": limit,
                "user_id": user_id,
                "all_documents": all_documents,
            }
        )
        rows = self.rows
        if statuses:
            rows = [(j, n) for j, n in rows if j.status in statuses]
        return rows[:limit]


def _job(status=JobStatus.QUEUED, attempts=0, error=None, document_id=None):
    job = Job(
        job_type=JobType.INGEST_DOCUMENT,
        document_id=document_id or uuid.uuid4(),
        status=status,
        attempts=attempts,
        error_message=error,
    )
    job.created_at = datetime(2026, 1, 1, 12, 0, 0)
    if status is not JobStatus.QUEUED:
        job.started_at = datetime(2026, 1, 1, 12, 0, 5)
    return job


@pytest.fixture
def jobs(monkeypatch):
    repository = _FakeJobRepository()
    monkeypatch.setattr(document_routes, "get_job_repository", lambda: repository)
    return repository


class _AlwaysAllows:
    async def check(self, key, limit, window_seconds, bucket):
        from src.governance.rate_limit import RateLimitResult

        return RateLimitResult(allowed=True, limit=limit, remaining=limit, reset_in=60)


@pytest.fixture
def client(monkeypatch):
    from src.governance.rbac import Principal, get_principal

    monkeypatch.setattr(dependencies_rate_limit, "get_rate_limiter", _AlwaysAllows)

    def _as(role: str = "analyst"):
        app.dependency_overrides[get_principal] = lambda: Principal(
            user_id=ME, role=role, clearance=Sensitivity.CONFIDENTIAL, email="me@test"
        )
        return TestClient(app)

    try:
        yield _as
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestQueue:
    def test_the_route_exists(self):
        """It did not, which is the reason this file exists."""
        assert "/api/v1/jobs" in set(app.openapi()["paths"])

    def test_a_job_carries_the_document_behind_it(self, client, jobs):
        """A queue of bare UUIDs cannot answer the question it is opened for."""
        job = _job(status=JobStatus.RUNNING, attempts=1)
        jobs.rows = [(job, "S-104 Rev C.pdf")]

        body = client().get("/api/v1/jobs").json()

        assert body[0]["document_name"] == "S-104 Rev C.pdf"
        assert body[0]["document_id"] == str(job.document_id)
        assert body[0]["status"] == "running"
        assert body[0]["attempts"] == 1
        assert body[0]["started_at"] is not None

    def test_a_failure_reports_why(self, client, jobs):
        jobs.rows = [
            (_job(status=JobStatus.FAILED, attempts=3, error="poppler not found"), "x.pdf")
        ]

        body = client().get("/api/v1/jobs").json()

        assert body[0]["status"] == "failed"
        assert body[0]["error_message"] == "poppler not found"
        assert body[0]["attempts"] == 3

    def test_a_job_whose_document_was_deleted_still_appears(self, client, jobs):
        """The join is left, not inner: history of what ran should not vanish
        because its subject did."""
        jobs.rows = [(_job(status=JobStatus.SUCCEEDED), None)]

        body = client().get("/api/v1/jobs").json()

        assert len(body) == 1
        assert body[0]["document_name"] is None

    def test_an_empty_queue_is_an_empty_list(self, client, jobs):
        assert client().get("/api/v1/jobs").json() == []


class TestFiltering:
    def test_status_narrows_the_queue(self, client, jobs):
        jobs.rows = [
            (_job(status=JobStatus.RUNNING), "a.pdf"),
            (_job(status=JobStatus.FAILED), "b.pdf"),
            (_job(status=JobStatus.SUCCEEDED), "c.pdf"),
        ]

        body = client().get("/api/v1/jobs?status=running&status=failed").json()

        assert {j["status"] for j in body} == {"running", "failed"}

    def test_an_unknown_status_is_ignored_not_rejected(self, client, jobs):
        """A queue view polls this on a timer. A 422 from a stale client
        would replace the queue with an error page rather than a slightly
        wrong filter."""
        jobs.rows = [(_job(status=JobStatus.RUNNING), "a.pdf")]

        response = client().get("/api/v1/jobs?status=running&status=not-a-status")

        assert response.status_code == 200
        assert jobs.calls[-1]["statuses"] == [JobStatus.RUNNING]

    def test_the_limit_is_passed_through(self, client, jobs):
        client().get("/api/v1/jobs?limit=5")

        assert jobs.calls[-1]["limit"] == 5


class TestScope:
    def test_the_queue_is_scoped_to_my_own_uploads(self, client, jobs):
        """A job row has no classification, so it inherits the reach of the
        document it refers to."""
        client("analyst").get("/api/v1/jobs")

        assert jobs.calls[-1]["user_id"] == ME
        assert jobs.calls[-1]["all_documents"] is False

    def test_an_admin_sees_the_whole_queue(self, client, jobs):
        """Operating the queue is the reason the role exists, and it matches
        the document list and detail routes."""
        client("admin").get("/api/v1/jobs")

        assert jobs.calls[-1]["all_documents"] is True
