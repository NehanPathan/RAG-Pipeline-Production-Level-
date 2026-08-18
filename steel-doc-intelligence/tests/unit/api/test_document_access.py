"""Who may read a document.

Two independent dimensions, and both must pass: *reach* (do you own it, or
share a project with it) and *clearance* (is it classified above you). This
file exists because the reach half was missing entirely -- the loader
checked only clearance, so `GET /documents/{id}` returned any document to
any authenticated caller who knew its id, as did `/chunks`,
`/intelligence` and `/original`, which streams the original file.

The list endpoint scoped strictly to `user_id` the whole time, so the
documents were hidden from the UI while remaining fully readable to anyone
who had ever seen an id -- in a citation, a log line, or a shared URL. The
two views disagreed about who could read what, and the more permissive one
was the one that returned the bytes.
"""

from __future__ import annotations

import uuid

import pytest

from src.api.routes import documents as document_routes
from src.domain.entities.document import Document
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.rbac import Principal

OWNER = uuid.uuid4()
COLLEAGUE = uuid.uuid4()
STRANGER = uuid.uuid4()
PROJECT = uuid.uuid4()


def _principal(user_id: uuid.UUID, role: str = "analyst", clearance=Sensitivity.CONFIDENTIAL):
    return Principal(user_id=user_id, role=role, clearance=clearance, email="x@test")


def _document(user_id: uuid.UUID = OWNER, project_id: uuid.UUID | None = None, **kwargs):
    return Document(
        file_name="S-104 Rev C.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        user_id=user_id,
        project_id=project_id,
        **kwargs,
    )


class _FakeDocumentRepository:
    def __init__(self, document: Document | None) -> None:
        self._document = document

    async def get_by_id(self, document_id):
        return self._document


class _FakeProjectRepository:
    def __init__(self, memberships: dict[uuid.UUID, list[uuid.UUID]] | None = None) -> None:
        self._memberships = memberships or {}
        self.fail = False

    async def member_project_ids(self, user_id):
        if self.fail:
            raise RuntimeError("projects table unavailable")
        return self._memberships.get(user_id, [])


@pytest.fixture
def wire(monkeypatch):
    """Point the route at fake repositories and silence the audit sink."""

    def _wire(document, memberships=None, fail_membership=False):
        projects = _FakeProjectRepository(memberships)
        projects.fail = fail_membership
        monkeypatch.setattr(
            document_routes, "get_document_repository", lambda: _FakeDocumentRepository(document)
        )
        monkeypatch.setattr(document_routes, "get_project_repository", lambda: projects)

        async def _no_audit(**kwargs):
            return None

        monkeypatch.setattr(document_routes, "audit_record", _no_audit)
        return projects

    return _wire


async def _load(document_id, principal):
    return await document_routes._load_readable_document(str(document_id), principal)


class TestReach:
    async def test_the_owner_can_read_their_own_document(self, wire):
        document = _document()
        wire(document)

        assert await _load(document.id, _principal(OWNER)) is document

    async def test_a_stranger_cannot_read_another_users_document(self, wire):
        """The leak. Knowing the id was previously enough."""
        document = _document()
        wire(document)

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(STRANGER))

        assert raised.value.status_code == 404

    async def test_the_refusal_is_404_not_403(self, wire):
        """A 403 confirms a document with that id exists, which is itself
        information the caller is not entitled to."""
        document = _document()
        wire(document)

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(STRANGER))

        assert raised.value.status_code == 404
        assert "not found" in str(raised.value.detail).lower()

    async def test_a_project_member_can_read_a_colleagues_document(self, wire):
        """The point of projects: two engineers on the same job see each
        other's drawings."""
        document = _document(user_id=OWNER, project_id=PROJECT)
        wire(document, memberships={COLLEAGUE: [PROJECT]})

        assert await _load(document.id, _principal(COLLEAGUE)) is document

    async def test_membership_of_a_different_project_does_not_help(self, wire):
        document = _document(user_id=OWNER, project_id=PROJECT)
        wire(document, memberships={STRANGER: [uuid.uuid4()]})

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(STRANGER))

        assert raised.value.status_code == 404

    async def test_a_personal_document_is_not_reachable_via_any_project(self, wire):
        """`project_id IS NULL` means personal. No membership can reach it."""
        document = _document(user_id=OWNER, project_id=None)
        wire(document, memberships={COLLEAGUE: [PROJECT]})

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(COLLEAGUE))

        assert raised.value.status_code == 404

    async def test_an_admin_may_read_anything(self, wire):
        """Deliberate: admins can already reclassify and delete any document,
        and the admin views would otherwise list what they cannot open. Every
        such read is audited under the actor's real role."""
        document = _document(user_id=OWNER)
        wire(document)

        assert await _load(document.id, _principal(STRANGER, role="admin")) is document

    async def test_a_failed_membership_lookup_denies_rather_than_widens(self, wire):
        """A database blip must not become an access leak."""
        document = _document(user_id=OWNER, project_id=PROJECT)
        wire(document, memberships={COLLEAGUE: [PROJECT]}, fail_membership=True)

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(COLLEAGUE))

        assert raised.value.status_code == 404


class TestClearanceStillApplies:
    """Reach and clearance are orthogonal. Passing one never excuses the other."""

    async def test_the_owner_cannot_read_above_their_clearance(self, wire):
        document = _document(user_id=OWNER, sensitivity=Sensitivity.RESTRICTED)
        wire(document)

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(OWNER, clearance=Sensitivity.INTERNAL))

        assert raised.value.status_code == 404

    async def test_a_project_member_cannot_read_above_their_clearance(self, wire):
        """Membership grants reach, never clearance."""
        document = _document(user_id=OWNER, project_id=PROJECT, sensitivity=Sensitivity.RESTRICTED)
        wire(document, memberships={COLLEAGUE: [PROJECT]})

        with pytest.raises(Exception) as raised:
            await _load(document.id, _principal(COLLEAGUE, clearance=Sensitivity.INTERNAL))

        assert raised.value.status_code == 404

    async def test_cleared_and_in_reach_succeeds(self, wire):
        document = _document(
            user_id=OWNER, project_id=PROJECT, sensitivity=Sensitivity.CONFIDENTIAL
        )
        wire(document, memberships={COLLEAGUE: [PROJECT]})

        assert await _load(document.id, _principal(COLLEAGUE)) is document


class TestMissing:
    async def test_an_unknown_document_is_404(self, wire):
        wire(None)

        with pytest.raises(Exception) as raised:
            await _load(uuid.uuid4(), _principal(OWNER))

        assert raised.value.status_code == 404

    async def test_a_malformed_id_is_422_not_404(self, wire):
        """A client bug and an access denial are different problems, and
        collapsing them makes the first one very hard to find."""
        wire(None)

        with pytest.raises(Exception) as raised:
            await document_routes._load_readable_document("not-a-uuid", _principal(OWNER))

        assert raised.value.status_code == 422
