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


def test_build_filter_includes_user_id(repo):
    user_id = uuid.uuid4()
    result = repo._build_filter(VectorSearchFilter(user_id=user_id))

    keys = [c.key for c in result.must]
    assert "user_id" in keys


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

    keys = {c.key for c in result.must}
    assert keys == {"user_id", "domain", "tags", "file_type", "document_id"}


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
