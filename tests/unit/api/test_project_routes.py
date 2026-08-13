"""Projects, members, drawings and revision history.

The tables and repositories existed all along; nothing reached them over
HTTP, so a project could only be created by a direct database write and a
drawing's revision history could not be read at all.

The access tests are the point of this file. Project membership is a scope,
and a non-member must get 404 rather than 403 -- a 403 confirms the project
exists, which is itself a leak when client names are commercially sensitive.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import projects as project_routes
from src.domain.entities.project import (
    Drawing,
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectStatus,
)


class _FakeProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, Project] = {}
        self.members: dict[uuid.UUID, list[ProjectMembership]] = {}

    async def save(self, project: Project) -> Project:
        self.projects[project.id] = project
        return project

    async def get_by_id(self, project_id):
        return self.projects.get(project_id)

    async def get_by_number(self, project_number):
        return next((p for p in self.projects.values() if p.project_number == project_number), None)

    async def list_for_user(self, user_id):
        ids = {m.project_id for ms in self.members.values() for m in ms if m.user_id == user_id}
        return [p for p in self.projects.values() if p.id in ids]

    async def add_member(self, project_id, user_id, role, added_by=None):
        self.members.setdefault(project_id, []).append(
            ProjectMembership(project_id=project_id, user_id=user_id, project_role=role)
        )

    async def remove_member(self, project_id, user_id):
        before = self.members.get(project_id, [])
        after = [m for m in before if m.user_id != user_id]
        self.members[project_id] = after
        return len(after) != len(before)

    async def list_members(self, project_id):
        return self.members.get(project_id, [])

    async def member_project_ids(self, user_id):
        return [m.project_id for ms in self.members.values() for m in ms if m.user_id == user_id]


class _FakeDrawingRepository:
    def __init__(self) -> None:
        self.drawings: dict[uuid.UUID, Drawing] = {}
        self.revisions: dict[uuid.UUID, list[uuid.UUID]] = {}
        self.current: dict[uuid.UUID, uuid.UUID] = {}

    async def list_for_project(self, project_id):
        return [d for d in self.drawings.values() if d.project_id == project_id]

    async def get_by_id(self, drawing_id):
        return self.drawings.get(drawing_id)

    async def current_revision_id(self, drawing_id):
        return self.current.get(drawing_id)

    async def revision_document_ids(self, drawing_id):
        return self.revisions.get(drawing_id, [])

    async def get_or_create(self, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError


class _FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents: dict[uuid.UUID, object] = {}

    async def get_by_id(self, document_id):
        return self.documents.get(document_id)


class _Doc:
    """Only the fields the revision list reads."""

    def __init__(self, revision_label, revision_index, is_latest, file_name="S-104.pdf"):
        self.id = uuid.uuid4()
        self.file_name = file_name
        self.revision_label = revision_label
        self.revision_index = revision_index
        self.is_latest = is_latest
        self.created_at = datetime(2026, 1, 1)
        self.superseded_by_document_id = None


class _AlwaysAllows:
    async def check(self, key, limit, window_seconds, bucket):
        from src.governance.rate_limit import RateLimitResult

        return RateLimitResult(allowed=True, limit=limit, remaining=limit, reset_in=60)


ME = uuid.uuid4()
SOMEONE_ELSE = uuid.uuid4()


@pytest.fixture
def repositories(monkeypatch):
    projects = _FakeProjectRepository()
    drawings = _FakeDrawingRepository()
    documents = _FakeDocumentRepository()
    monkeypatch.setattr(project_routes, "get_project_repository", lambda: projects)
    monkeypatch.setattr(project_routes, "get_drawing_repository", lambda: drawings)
    monkeypatch.setattr(project_routes, "get_document_repository", lambda: documents)
    return projects, drawings, documents


@pytest.fixture
def client(monkeypatch):
    from src.api import dependencies_rate_limit
    from src.domain.value_objects.sensitivity import Sensitivity
    from src.governance.rbac import Principal, get_principal

    monkeypatch.setattr(dependencies_rate_limit, "get_rate_limiter", _AlwaysAllows)
    app.dependency_overrides[get_principal] = lambda: Principal(
        user_id=ME, role="analyst", clearance=Sensitivity.CONFIDENTIAL, email="me@test"
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _existing_project(repositories, *, member_role: ProjectRole | None = ProjectRole.OWNER):
    projects, _, _ = repositories
    project = Project(project_number="2024-0117", name="Warehouse", client_name="Acme")
    projects.projects[project.id] = project
    if member_role is not None:
        projects.members[project.id] = [
            ProjectMembership(project_id=project.id, user_id=ME, project_role=member_role)
        ]
    else:
        projects.members[project.id] = [
            ProjectMembership(
                project_id=project.id, user_id=SOMEONE_ELSE, project_role=ProjectRole.OWNER
            )
        ]
    return project


class TestCreate:
    def test_creating_a_project_makes_the_caller_its_owner(self, client, repositories):
        """A project whose creator cannot see it is a state only ever
        discovered by a confused user."""
        response = client.post(
            "/api/v1/projects", json={"project_number": "2024-0117", "name": "Warehouse"}
        )

        assert response.status_code == 201
        assert response.json()["my_role"] == "owner"

        mine = client.get("/api/v1/projects").json()
        assert [p["project_number"] for p in mine] == ["2024-0117"]

    def test_a_duplicate_project_number_is_rejected(self, client, repositories):
        _existing_project(repositories)

        response = client.post(
            "/api/v1/projects", json={"project_number": "2024-0117", "name": "Another"}
        )

        assert response.status_code == 409

    def test_the_project_list_is_never_global(self, client, repositories):
        """Someone else's project must not appear just because it exists."""
        _existing_project(repositories, member_role=None)

        assert client.get("/api/v1/projects").json() == []


class TestAccessScope:
    """A non-member gets 404, not 403. A 403 confirms the project exists."""

    def test_a_non_member_cannot_see_a_project(self, client, repositories):
        project = _existing_project(repositories, member_role=None)

        assert client.get(f"/api/v1/projects/{project.id}").status_code == 404

    def test_a_non_member_cannot_list_members(self, client, repositories):
        project = _existing_project(repositories, member_role=None)

        assert client.get(f"/api/v1/projects/{project.id}/members").status_code == 404

    def test_a_non_member_cannot_see_the_drawing_register(self, client, repositories):
        project = _existing_project(repositories, member_role=None)

        assert client.get(f"/api/v1/projects/{project.id}/drawings").status_code == 404

    def test_a_non_member_cannot_add_themselves(self, client, repositories):
        """The obvious privilege escalation, and it must not read as 403."""
        project = _existing_project(repositories, member_role=None)

        response = client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": str(ME), "role": "owner"},
        )

        assert response.status_code == 404

    def test_a_reader_cannot_widen_access(self, client, repositories):
        """Adding a member is not a read-only operation: it widens who can
        see a client's drawings. A reader is already a member, so 403 here
        leaks nothing."""
        project = _existing_project(repositories, member_role=ProjectRole.READER)

        response = client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": str(SOMEONE_ELSE), "role": "reader"},
        )

        assert response.status_code == 403

    def test_a_contributor_may_add_a_member(self, client, repositories):
        project = _existing_project(repositories, member_role=ProjectRole.CONTRIBUTOR)

        response = client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": str(SOMEONE_ELSE), "role": "reader"},
        )

        assert response.status_code == 204
        members = client.get(f"/api/v1/projects/{project.id}/members").json()
        assert {m["user_id"] for m in members} == {str(ME), str(SOMEONE_ELSE)}

    def test_a_member_can_be_removed(self, client, repositories):
        project = _existing_project(repositories)
        client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": str(SOMEONE_ELSE), "role": "reader"},
        )

        response = client.delete(f"/api/v1/projects/{project.id}/members/{SOMEONE_ELSE}")

        assert response.status_code == 204
        members = client.get(f"/api/v1/projects/{project.id}/members").json()
        assert {m["user_id"] for m in members} == {str(ME)}


class TestDrawingRegister:
    def test_one_row_per_drawing_not_per_revision(self, client, repositories):
        """S-104 appears once however many revisions exist. That distinction
        is what the drawings table is for."""
        _, drawings, documents = repositories
        project = _existing_project(repositories)
        drawing = Drawing(drawing_number="S-104", project_id=project.id, title="Roof Framing")
        drawings.drawings[drawing.id] = drawing

        rev_b = _Doc("B", 2, is_latest=False)
        rev_c = _Doc("C", 3, is_latest=True)
        for doc in (rev_b, rev_c):
            documents.documents[doc.id] = doc
        drawings.revisions[drawing.id] = [rev_b.id, rev_c.id]
        drawings.current[drawing.id] = rev_c.id

        register = client.get(f"/api/v1/projects/{project.id}/drawings").json()

        assert len(register) == 1
        assert register[0]["drawing_number"] == "S-104"
        assert register[0]["revision_count"] == 2
        assert register[0]["current_revision_label"] == "C"
        assert register[0]["current_document_id"] == str(rev_c.id)

    def test_revisions_are_ordered_by_revision_not_upload_time(self, client, repositories):
        """Drawing sets arrive out of sequence -- someone finds Rev B after
        Rev C is already in. Ordering by arrival would present the register
        wrongly in exactly the case it matters."""
        _, drawings, documents = repositories
        project = _existing_project(repositories)
        drawing = Drawing(drawing_number="S-104", project_id=project.id)
        drawings.drawings[drawing.id] = drawing

        rev_c = _Doc("C", 3, is_latest=True)
        rev_a = _Doc("A", 1, is_latest=False)
        rev_b = _Doc("B", 2, is_latest=False)
        # Uploaded C, then A, then B.
        for doc in (rev_c, rev_a, rev_b):
            documents.documents[doc.id] = doc
        drawings.revisions[drawing.id] = [rev_c.id, rev_a.id, rev_b.id]

        history = client.get(f"/api/v1/drawings/{drawing.id}/revisions").json()

        assert [r["revision_label"] for r in history] == ["C", "B", "A"]
        assert [r["is_latest"] for r in history] == [True, False, False]

    def test_a_non_member_cannot_read_revision_history(self, client, repositories):
        _, drawings, _ = repositories
        project = _existing_project(repositories, member_role=None)
        drawing = Drawing(drawing_number="S-104", project_id=project.id)
        drawings.drawings[drawing.id] = drawing

        assert client.get(f"/api/v1/drawings/{drawing.id}/revisions").status_code == 404

    def test_an_unknown_drawing_is_404(self, client, repositories):
        assert client.get(f"/api/v1/drawings/{uuid.uuid4()}/revisions").status_code == 404


class TestShape:
    def test_a_project_carries_the_fields_a_register_ui_needs(self, client, repositories):
        project = _existing_project(repositories)
        project.status = ProjectStatus.ACTIVE

        body = client.get(f"/api/v1/projects/{project.id}").json()

        assert body["project_number"] == "2024-0117"
        assert body["name"] == "Warehouse"
        assert body["client_name"] == "Acme"
        assert body["status"] == "active"
        assert body["my_role"] == "owner"
