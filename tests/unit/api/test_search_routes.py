"""The structured-search endpoints.

These had no tests at all: the repository layer was covered, the routes were
not, and both of the bugs found here were in the route -- the router was
registered but never `include_router`'d, and the access filter could be
widened by the client. Neither is visible from a repository test.

The repositories are faked, because what is under test is the translation
from request to filter: which fields are honoured, and -- far more
importantly -- which are not.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.api import dependencies, dependencies_rate_limit
from src.api.main import app
from src.api.routes import search as search_routes
from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.value_objects.sensitivity import Sensitivity


class _FakeSearchRepository:
    def __init__(self) -> None:
        self.last_query: str | None = None
        self.last_filters = None
        self.facet_fields: list[str] | None = None

    async def search(self, query, top_k=20, filters=None):
        from src.domain.repositories.search_repository import BM25ScoredChunk

        self.last_query = query
        self.last_filters = filters
        chunk = DocumentChunk(
            document_id=uuid.uuid4(),
            content="B-14 ISMB 300 spanning 6000 mm",
            position=0,
            chunk_type=ChunkType.CHILD,
            chunk_metadata=ChunkMetadata(
                page_number=3, section_title="Beam Schedule", content_kind="vector_drawing"
            ),
            drawing_number="S-104",
            revision_label="C",
            project_number="2024-0117",
            document_name="S-104 Rev C.pdf",
            sensitivity=Sensitivity.INTERNAL,
        )
        return [BM25ScoredChunk(chunk=chunk, bm25_score=4.2, rank=1)]

    async def aggregate_facets(self, fields, filters=None, max_values=50):
        self.facet_fields = list(fields)
        self.last_filters = filters
        return {field: [(f"{field}-value", 3)] for field in fields}


class _FakeProjectRepository:
    def __init__(self, project_ids=None, fail=False) -> None:
        self._project_ids = project_ids or []
        self._fail = fail

    async def member_project_ids(self, user_id):
        if self._fail:
            raise RuntimeError("projects table unavailable")
        return list(self._project_ids)


@pytest.fixture
def repositories(monkeypatch):
    search_repo = _FakeSearchRepository()
    project_repo = _FakeProjectRepository()
    monkeypatch.setattr(dependencies, "get_search_repository", lambda: search_repo)
    monkeypatch.setattr(dependencies, "get_project_repository", lambda: project_repo)
    monkeypatch.setattr(search_routes, "get_search_repository", lambda: search_repo)
    monkeypatch.setattr(search_routes, "get_project_repository", lambda: project_repo)
    return search_repo, project_repo


class _AlwaysAllows:
    """A rate limiter that never refuses and never touches Redis."""

    async def check(self, key, limit, window_seconds, bucket):
        from src.governance.rate_limit import RateLimitResult

        return RateLimitResult(allowed=True, limit=limit, remaining=limit, reset_in=60)


@pytest.fixture
def client(monkeypatch):
    """A client authenticated as an analyst, with the limiter stubbed.

    Not used as a context manager on purpose: entering it runs the app
    lifespan, which connects to Postgres, Qdrant, Redis and Elasticsearch and
    warms the embedding model. None of that is needed to test how a request
    becomes a filter.

    The principal is overridden rather than faked with headers, because
    header-based dev identity still reads the role from the database.
    """
    from src.governance.rbac import Principal, get_principal

    monkeypatch.setattr(dependencies_rate_limit, "get_rate_limiter", _AlwaysAllows)
    app.dependency_overrides[get_principal] = lambda: Principal(
        user_id=uuid.uuid4(),
        role="analyst",
        clearance=Sensitivity.CONFIDENTIAL,
        email="analyst@test",
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestRoutesExist:
    """Regression: the router was imported and never included, so both
    endpoints 404'd -- and ruff then removed the import as unused."""

    def test_both_endpoints_are_registered(self):
        paths = set(app.openapi()["paths"])

        assert "/api/v1/search" in paths
        assert "/api/v1/search/facets" in paths


class TestStructuredSearch:
    def test_a_hit_carries_its_page_drawing_and_provenance(self, client, repositories):
        response = client.post("/api/v1/search", json={"query": "ISMB 300"})

        assert response.status_code == 200
        hit = response.json()["items"][0]
        assert hit["page_number"] == 3
        assert hit["drawing_number"] == "S-104"
        assert hit["revision_label"] == "C"
        assert hit["content_kind"] == "vector_drawing"

    def test_an_empty_query_is_a_legitimate_filter_only_search(self, client, repositories):
        """ "Everything in this project referencing ISMB 300" is a filter, not
        a question. The repository turns `*` into match_all."""
        search_repo, _ = repositories

        response = client.post("/api/v1/search", json={"query": "", "tags": ["ismb-300"]})

        assert response.status_code == 200
        assert search_repo.last_query == "*"

    def test_filters_are_passed_through(self, client, repositories):
        search_repo, _ = repositories

        client.post(
            "/api/v1/search",
            json={
                "query": "beam",
                "drawing_numbers": ["S-104"],
                "entity_canonicals": ["ISMB 300"],
                "content_kinds": ["vector_drawing", "scanned_drawing"],
                "tags": ["structural"],
                "file_type": "pdf",
                "domain": "Engineering",
            },
        )

        filters = search_repo.last_filters
        assert filters.drawing_numbers == ["S-104"]
        assert filters.entity_canonicals == ["ISMB 300"]
        assert filters.content_kinds == ["vector_drawing", "scanned_drawing"]
        assert filters.tags == ["structural"]
        assert filters.file_type == "pdf"
        assert filters.domain == "Engineering"

    def test_superseded_revisions_are_excluded_by_default(self, client, repositories):
        """Answering from an old sheet is worse than not answering."""
        search_repo, _ = repositories

        client.post("/api/v1/search", json={"query": "beam"})

        assert search_repo.last_filters.latest_only is True

    def test_superseded_revisions_can_be_asked_for(self, client, repositories):
        search_repo, _ = repositories

        client.post("/api/v1/search", json={"query": "beam", "include_superseded": True})

        assert search_repo.last_filters.latest_only is False


class TestAccessScopeCannotBeWidened:
    """The sensitive part. A client must not be able to reach a project it
    does not belong to by naming it in the request body."""

    def test_requested_projects_are_intersected_with_membership(
        self, client, repositories, monkeypatch
    ):
        mine = uuid.uuid4()
        theirs = uuid.uuid4()
        project_repo = _FakeProjectRepository(project_ids=[mine])
        monkeypatch.setattr(search_routes, "get_project_repository", lambda: project_repo)
        search_repo, _ = repositories

        client.post(
            "/api/v1/search", json={"query": "beam", "project_ids": [str(mine), str(theirs)]}
        )

        assert search_repo.last_filters.project_ids == [mine]

    def test_asking_only_for_a_project_you_are_not_in_matches_nothing(
        self, client, repositories, monkeypatch
    ):
        project_repo = _FakeProjectRepository(project_ids=[uuid.uuid4()])
        monkeypatch.setattr(search_routes, "get_project_repository", lambda: project_repo)
        search_repo, _ = repositories

        client.post("/api/v1/search", json={"query": "beam", "project_ids": [str(uuid.uuid4())]})

        assert search_repo.last_filters.project_ids == []

    def test_a_degraded_membership_lookup_fails_closed(self, client, repositories, monkeypatch):
        """A lookup failure narrows reach to owner-only; it never widens it."""
        monkeypatch.setattr(
            search_routes, "get_project_repository", lambda: _FakeProjectRepository(fail=True)
        )
        search_repo, _ = repositories

        response = client.post("/api/v1/search", json={"query": "beam"})

        assert response.status_code == 200
        assert search_repo.last_filters.project_ids is None

    def test_the_clearance_ceiling_is_always_applied(self, client, repositories):
        search_repo, _ = repositories

        client.post("/api/v1/search", json={"query": "beam"})

        assert search_repo.last_filters.sensitivity_in


class TestFacets:
    def test_facets_return_the_corpus_vocabulary(self, client, repositories):
        response = client.get("/api/v1/search/facets", params={"fields": ["drawing_number"]})

        assert response.status_code == 200
        assert response.json()["facets"]["drawing_number"] == [
            {"value": "drawing_number-value", "count": 3}
        ]

    def test_content_kind_is_offered_as_a_facet(self, client, repositories):
        """It is what separates an exact CAD dimension from an OCR'd one."""
        response = client.get("/api/v1/search/facets")

        assert "content_kind" in response.json()["facets"]

    def test_unknown_fields_are_ignored_rather_than_queried(self, client, repositories):
        """A facet list is built from a fixed set: passing an arbitrary field
        would let a client aggregate over anything in the index."""
        search_repo, _ = repositories

        client.get("/api/v1/search/facets", params={"fields": ["content", "drawing_number"]})

        assert search_repo.facet_fields == ["drawing_number"]

    def test_facets_are_computed_inside_the_access_filter(self, client, repositories):
        """A facet list must never reveal that a project or drawing exists
        which the caller cannot read."""
        search_repo, _ = repositories

        client.get("/api/v1/search/facets")

        assert search_repo.last_filters is not None
        assert search_repo.last_filters.sensitivity_in


# --- Conversation history is bounded and scoped -----------------------------


class TestConversationHistory:
    """What a follow-up may see.

    Two properties, and the second is a security one: history is bounded so a
    long conversation cannot grow every request without limit, and it is
    scoped to a conversation the caller owns so a passed-in id cannot fold
    someone else's turns into the caller's query rewrite.
    """

    async def test_history_is_bounded(self, monkeypatch):
        from src.api.routes import chat as chat_routes

        class _Repo:
            async def owns(self, conversation_id, user_id):
                return True

            async def get_messages(self, conversation_id):
                from types import SimpleNamespace

                return [
                    SimpleNamespace(role=SimpleNamespace(value="user"), content=f"turn {i}")
                    for i in range(50)
                ]

        monkeypatch.setattr(chat_routes, "get_conversation_repository", lambda: _Repo())
        principal = _principal_for_history()

        history = await chat_routes._recent_history(uuid.uuid4(), principal)

        assert len(history) == chat_routes.MAX_HISTORY_TURNS
        assert history[-1] == ("user", "turn 49"), "the most recent turns, not the oldest"

    async def test_a_conversation_the_caller_does_not_own_yields_nothing(self, monkeypatch):
        """Otherwise a caller could pass someone else's conversation_id and
        have its content folded into their query rewrite -- an information
        leak through a feature that looks like convenience."""
        from src.api.routes import chat as chat_routes

        class _Repo:
            async def owns(self, conversation_id, user_id):
                return False

            async def get_messages(self, conversation_id):  # pragma: no cover
                raise AssertionError("must not read messages of an unowned conversation")

        monkeypatch.setattr(chat_routes, "get_conversation_repository", lambda: _Repo())

        assert await chat_routes._recent_history(uuid.uuid4(), _principal_for_history()) == []

    async def test_an_unavailable_repository_degrades_rather_than_fails(self, monkeypatch):
        from src.api.routes import chat as chat_routes

        class _Repo:
            async def owns(self, conversation_id, user_id):
                raise RuntimeError("database down")

        monkeypatch.setattr(chat_routes, "get_conversation_repository", lambda: _Repo())

        assert await chat_routes._recent_history(uuid.uuid4(), _principal_for_history()) == []


def _principal_for_history():
    from src.governance.rbac import Principal

    return Principal(
        user_id=uuid.uuid4(), role="analyst", clearance=Sensitivity.CONFIDENTIAL, email="a@b"
    )
