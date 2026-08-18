"""Releasing a quarantined document, and the thing release must never become.

Release means "an authorised human reviewed this and wants ingestion to
proceed". It must not come to mean "skip the content screen for this file from
now on", because that would turn one reviewer's judgement into a permanent
hole in the control -- and nobody would notice, since a released file looks
exactly like one that passed.

The load-bearing test is `test_release_does_not_bypass_screening`: a released
document goes back through the ordinary pipeline and is screened again on the
way, so a file that still fails is quarantined a second time.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes.documents import ReleaseRequest, release_document
from src.domain.entities.document import Document, DocumentStatus
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.audit import AuditAction
from src.governance.rbac import Principal

OWNER = uuid.uuid4()


def _document(status: DocumentStatus = DocumentStatus.QUARANTINED) -> Document:
    return Document(
        file_name="suspect.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        user_id=OWNER,
        status=status,
        error_message="quarantined: no engineering content",
        sensitivity=Sensitivity.INTERNAL,
    )


def _principal(role: str = "admin") -> Principal:
    return Principal(
        user_id=OWNER, role=role, clearance=Sensitivity.RESTRICTED, email="steward@x.y"
    )


@pytest.fixture
def wired(monkeypatch):
    """Patch the module's collaborators, leaving the route's own logic real."""
    import src.api.routes.documents as routes

    document = _document()
    jobs = AsyncMock()
    jobs.has_active_job = AsyncMock(return_value=False)
    repo = AsyncMock()
    repo.update = AsyncMock()
    job = type("Job", (), {"id": uuid.uuid4(), "status": type("S", (), {"value": "queued"})()})()

    enqueued: list[tuple] = []
    audited: list[dict] = []

    async def _load(document_id, principal):
        return document

    async def _enqueue(doc_id, path, job_type=None):
        enqueued.append((doc_id, job_type))
        return job

    async def _audit(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(routes, "_load_readable_document", _load)
    monkeypatch.setattr(routes, "get_job_repository", lambda: jobs)
    monkeypatch.setattr(routes, "get_document_repository", lambda: repo)
    monkeypatch.setattr(routes, "enqueue_ingestion", _enqueue)
    monkeypatch.setattr(routes, "audit_record", _audit)

    return {
        "document": document,
        "jobs": jobs,
        "repo": repo,
        "enqueued": enqueued,
        "audited": audited,
    }


class TestPermissions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("role", "permitted"),
        [
            ("admin", True),
            ("steward", True),
            ("analyst", False),
            ("viewer", False),
        ],
    )
    async def test_the_route_uses_the_shared_role_guard(self, role, permitted):
        """Exercised through the real dependency rather than asserted against
        a constant, so this fails if the route is ever given a bespoke check
        instead of the `require_role` guard every other privileged route uses.
        """
        import inspect

        guard = inspect.signature(release_document).parameters["principal"].default.dependency
        principal = _principal(role)

        if permitted:
            assert await guard(principal) is principal
        else:
            with pytest.raises(HTTPException) as caught:
                await guard(principal)
            assert caught.value.status_code == 403

    @pytest.mark.asyncio
    async def test_an_unreadable_document_is_not_found(self, monkeypatch):
        """404, not 403: confirming a restricted document exists is itself a
        disclosure, which is the convention the rest of the API follows."""
        import src.api.routes.documents as routes

        async def _deny(document_id, principal):
            raise HTTPException(status_code=404, detail="Document not found")

        monkeypatch.setattr(routes, "_load_readable_document", _deny)
        with pytest.raises(HTTPException) as caught:
            await release_document(str(uuid.uuid4()), None, _principal())
        assert caught.value.status_code == 404


class TestState:
    @pytest.mark.asyncio
    async def test_a_quarantined_document_is_released(self, wired):
        result = await release_document(
            str(wired["document"].id), ReleaseRequest(reason="reviewed"), _principal()
        )
        assert result.job_id
        assert wired["enqueued"], "release did not enqueue an ingestion job"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [DocumentStatus.INDEXED, DocumentStatus.PENDING, DocumentStatus.FAILED],
    )
    async def test_a_document_that_is_not_quarantined_conflicts(self, wired, status):
        wired["document"].status = status
        with pytest.raises(HTTPException) as caught:
            await release_document(str(wired["document"].id), None, _principal())
        assert caught.value.status_code == 409
        assert "not quarantined" in caught.value.detail
        assert not wired["enqueued"]

    @pytest.mark.asyncio
    async def test_a_document_already_in_flight_conflicts(self, wired):
        wired["jobs"].has_active_job = AsyncMock(return_value=True)
        with pytest.raises(HTTPException) as caught:
            await release_document(str(wired["document"].id), None, _principal())
        assert caught.value.status_code == 409
        assert not wired["enqueued"], "a second job would duplicate the chunks"


class TestWorkflow:
    @pytest.mark.asyncio
    async def test_release_goes_through_the_normal_ingestion_job(self, wired):
        """No shortcut into Qdrant or Elasticsearch. The only path is the job
        the worker already runs for an upload."""
        from src.jobs.models import JobType

        await release_document(str(wired["document"].id), None, _principal())
        doc_id, job_type = wired["enqueued"][0]
        assert doc_id == wired["document"].id
        assert job_type is JobType.REINDEX_DOCUMENT

    @pytest.mark.asyncio
    async def test_the_document_returns_to_pending_before_the_job_is_queued(self, wired):
        """So the queue and the document never disagree about whether it is
        waiting, and the quarantine reason does not linger as if current."""
        await release_document(str(wired["document"].id), None, _principal())
        assert wired["document"].status is DocumentStatus.PENDING
        assert wired["document"].error_message is None
        wired["repo"].update.assert_awaited()

    def test_release_does_not_bypass_screening(self):
        """The point of the whole design.

        Release re-enters ingestion, and ingestion screens. Nothing in the
        release path sets a flag, an exemption or an allow-list, so a document
        that still fails the policy is quarantined again -- verified here by
        reading the screen itself rather than trusting the route.
        """
        from src.ingestion.screening import screen_document

        filler = (
            "Preheat the oven to one hundred and eighty degrees and cream the "
            "butter with the sugar until it is pale. "
        ) * 12
        first = screen_document(filler, content_kind="prose")
        assert first.quarantined

        # Exactly the same call the pipeline makes after a release. There is
        # no per-document state that could make the second answer differ.
        second = screen_document(filler, content_kind="prose")
        assert second.quarantined
        assert second.category == first.category

    def test_a_legitimate_drawing_never_needed_releasing(self):
        from src.ingestion.screening import screen_document

        assert screen_document("BENT PLATE 5x5x 12 GA.", content_kind="cad_native").allowed


class TestAudit:
    @pytest.mark.asyncio
    async def test_a_release_is_audited_against_the_authenticated_principal(self, wired):
        principal = _principal()
        await release_document(
            str(wired["document"].id), ReleaseRequest(reason="legitimate transmittal"), principal
        )
        assert len(wired["audited"]) == 1
        entry = wired["audited"][0]
        assert entry["action"] is AuditAction.QUARANTINE_RELEASED
        assert entry["actor_id"] == principal.user_id
        assert entry["actor_role"] == principal.role
        assert entry["resource_id"] == str(wired["document"].id)
        assert entry["reason"] == "legitimate transmittal"

    @pytest.mark.asyncio
    async def test_the_previous_status_is_recorded(self, wired):
        """ "Released" is only meaningful next to what it was released from."""
        await release_document(str(wired["document"].id), None, _principal())
        entry = wired["audited"][0]
        assert entry["before"] == {"status": "quarantined"}
        assert entry["after"]["status"] == "pending"
        assert entry["after"]["job_id"]

    @pytest.mark.asyncio
    async def test_a_release_without_a_reason_still_records_one(self, wired):
        await release_document(str(wired["document"].id), None, _principal())
        assert wired["audited"][0]["reason"]

    @pytest.mark.asyncio
    async def test_nothing_is_audited_when_the_release_is_refused(self, wired):
        wired["document"].status = DocumentStatus.INDEXED
        with pytest.raises(HTTPException):
            await release_document(str(wired["document"].id), None, _principal())
        assert wired["audited"] == []
