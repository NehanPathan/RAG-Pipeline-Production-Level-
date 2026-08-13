import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import AsyncQdrantClient

from src.domain.entities.document import ChunkType, DocumentChunk
from src.domain.repositories.vector_repository import VectorSearchFilter
from src.infrastructure.vector_store.qdrant.repository import QdrantVectorRepository


@pytest.fixture
def mock_client():
    # spec=AsyncQdrantClient so calling a method that doesn't exist on the
    # real client (e.g. the removed .search()) raises AttributeError here
    # instead of silently succeeding against an unconstrained AsyncMock.
    return AsyncMock(spec=AsyncQdrantClient)


@pytest.fixture
def repo(mock_client):
    return QdrantVectorRepository(client=mock_client, collection_name="document_chunks")


def test_build_filter_returns_none_when_no_filters(repo):
    assert repo._build_filter(None) is None


def _condition_keys(qdrant_filter) -> set[str]:
    """Flatten one level of nesting.

    The reachability clause is a `should` group inside `must` -- owner OR
    project member -- so its keys are one level down from the flat
    conditions.
    """
    keys: set[str] = set()
    for condition in qdrant_filter.must or []:
        if getattr(condition, "key", None) is not None:
            keys.add(condition.key)
        for nested in getattr(condition, "should", None) or []:
            if getattr(nested, "key", None) is not None:
                keys.add(nested.key)
    return keys


def test_build_filter_includes_user_id(repo):
    user_id = uuid.uuid4()
    result = repo._build_filter(VectorSearchFilter(user_id=user_id))

    assert "user_id" in _condition_keys(result)


class TestAccessScope:
    """Reachability is owner OR project member, and it must narrow as a unit.

    Two separate `must` conditions would mean "owned AND in a project", which
    hides every document a caller owns outside their projects.
    """

    def test_owner_and_projects_are_one_should_group(self, repo):
        result = repo._build_filter(
            VectorSearchFilter(user_id=uuid.uuid4(), project_ids=[uuid.uuid4()])
        )

        groups = [c for c in result.must if getattr(c, "should", None)]
        reachability = [g for g in groups if {n.key for n in g.should} == {"user_id", "project_id"}]
        assert len(reachability) == 1, "owner and project must share one should group"

    def test_projects_alone_still_filter(self, repo):
        result = repo._build_filter(VectorSearchFilter(project_ids=[uuid.uuid4()]))

        assert "project_id" in _condition_keys(result)

    def test_no_scope_produces_no_reachability_clause(self, repo):
        result = repo._build_filter(VectorSearchFilter(domain="HR"))

        assert "user_id" not in _condition_keys(result)
        assert "project_id" not in _condition_keys(result)


class TestLatestOnly:
    def test_latest_only_admits_rows_predating_revisions(self, repo):
        """Points indexed before revisions existed carry no `is_latest` key
        and are current by definition; a bare match would hide the entire
        pre-revision corpus."""
        result = repo._build_filter(VectorSearchFilter(latest_only=True))

        groups = [c for c in result.must if getattr(c, "should", None)]
        latest = [g for g in groups if any(getattr(n, "key", "") == "is_latest" for n in g.should)]
        assert latest, "is_latest must be filtered"
        assert any(
            getattr(n, "is_null", None) is not None for n in latest[0].should
        ), "missing is_latest must be treated as current"

    def test_off_by_default(self, repo):
        result = repo._build_filter(VectorSearchFilter(domain="HR"))

        assert "is_latest" not in _condition_keys(result)


def test_build_filter_includes_file_type(repo):
    result = repo._build_filter(VectorSearchFilter(file_type="pdf"))

    keys = [c.key for c in result.must]
    assert "file_type" in keys


def test_build_filter_includes_document_ids(repo):
    doc_id = uuid.uuid4()
    result = repo._build_filter(VectorSearchFilter(document_ids=[doc_id]))

    keys = [c.key for c in result.must]
    assert "document_id" in keys


def test_build_filter_combines_all_conditions(repo):
    result = repo._build_filter(
        VectorSearchFilter(
            user_id=uuid.uuid4(),
            domain="HR",
            tags=["policy"],
            file_type="pdf",
            document_ids=[uuid.uuid4()],
        )
    )

    assert _condition_keys(result) == {
        "user_id",
        "domain",
        "tags",
        "file_type",
        "document_id",
    }


@pytest.mark.asyncio
async def test_upsert_batch_writes_denormalized_tenant_fields(repo, mock_client):
    user_id = uuid.uuid4()
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="hello",
        position=0,
        chunk_type=ChunkType.CHILD,
        embedding=[0.1, 0.2],
        user_id=user_id,
        domain="HR",
        tags=["policy"],
        file_type="pdf",
    )

    await repo.upsert_batch([chunk])

    mock_client.upsert.assert_called_once()
    point = mock_client.upsert.call_args.kwargs["points"][0]
    assert point.payload["user_id"] == str(user_id)
    assert point.payload["domain"] == "HR"
    assert point.payload["tags"] == ["policy"]
    assert point.payload["file_type"] == "pdf"


@pytest.mark.asyncio
async def test_create_collection_creates_file_type_payload_index(repo, mock_client):
    mock_client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))

    await repo.create_collection_if_not_exists(vector_size=1536)

    index_field_names = [
        call.kwargs["field_name"] for call in mock_client.create_payload_index.call_args_list
    ]
    assert "file_type" in index_field_names


@pytest.mark.asyncio
async def test_search_reconstructs_denormalized_fields_from_payload(repo, mock_client):
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    hit = MagicMock(
        id=str(chunk_id),
        score=0.87,
        payload={
            "document_id": str(document_id),
            "content": "hello world",
            "position": 2,
            "chunk_type": "child",
            "token_count": 12,
            "page_number": 5,
            "section": "Section 3.2",
            "contains_table": True,
            "parent_chunk_id": str(parent_id),
            "user_id": str(user_id),
            "domain": "HR",
            "tags": ["policy"],
            "file_type": "pdf",
            "document_name": "hr_policy.pdf",
        },
    )
    mock_client.query_points = AsyncMock(return_value=MagicMock(points=[hit]))

    results = await repo.search(query_vector=[0.1, 0.2], top_k=5)

    chunk = results[0].chunk
    assert chunk.id == chunk_id
    assert chunk.parent_chunk_id == parent_id
    assert chunk.chunk_metadata.page_number == 5
    assert chunk.chunk_metadata.section == "Section 3.2"
    assert chunk.chunk_metadata.contains_table is True
    assert chunk.user_id == user_id
    assert chunk.domain == "HR"
    assert chunk.tags == ["policy"]
    assert chunk.file_type == "pdf"
    assert chunk.document_name == "hr_policy.pdf"
