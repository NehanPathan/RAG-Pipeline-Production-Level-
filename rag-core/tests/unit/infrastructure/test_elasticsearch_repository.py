import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import ChunkType, DocumentChunk
from src.domain.repositories.search_repository import BM25SearchFilter
from src.infrastructure.search.elasticsearch.repository import (
    INDEX_MAPPINGS,
    ElasticsearchSearchRepository,
)


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def repo(mock_client):
    return ElasticsearchSearchRepository(client=mock_client, index_name="document_chunks")


def test_index_mappings_includes_file_type():
    assert INDEX_MAPPINGS["mappings"]["properties"]["file_type"] == {"type": "keyword"}


def test_build_filter_clauses_empty_when_no_filters(repo):
    assert repo._build_filter_clauses(None) == []


def test_build_filter_clauses_includes_file_type(repo):
    clauses = repo._build_filter_clauses(BM25SearchFilter(file_type="pdf"))

    assert {"term": {"file_type": "pdf"}} in clauses


def test_build_filter_clauses_includes_document_ids(repo):
    doc_id = uuid.uuid4()
    clauses = repo._build_filter_clauses(BM25SearchFilter(document_ids=[doc_id]))

    assert {"terms": {"document_id": [str(doc_id)]}} in clauses


def test_build_filter_clauses_combines_all(repo):
    clauses = repo._build_filter_clauses(
        BM25SearchFilter(
            user_id=uuid.uuid4(),
            domain="HR",
            tags=["policy"],
            file_type="pdf",
            document_ids=[uuid.uuid4()],
        )
    )

    assert len(clauses) == 5


@pytest.mark.asyncio
async def test_index_batch_writes_denormalized_tenant_fields(repo, mock_client):
    user_id = uuid.uuid4()
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="hello",
        position=0,
        chunk_type=ChunkType.CHILD,
        user_id=user_id,
        domain="HR",
        tags=["policy"],
        file_type="pdf",
    )
    mock_client.bulk = AsyncMock(return_value={"errors": False, "items": []})

    await repo.index_batch([chunk])

    mock_client.bulk.assert_called_once()
    operations = mock_client.bulk.call_args.kwargs["operations"]
    doc_body = operations[1]
    assert doc_body["user_id"] == str(user_id)
    assert doc_body["domain"] == "HR"
    assert doc_body["tags"] == ["policy"]
    assert doc_body["file_type"] == "pdf"


@pytest.mark.asyncio
async def test_search_reconstructs_denormalized_fields_from_source(repo, mock_client):
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    mock_client.search = AsyncMock(
        return_value={
            "hits": {
                "hits": [
                    {
                        "_score": 12.5,
                        "_source": {
                            "chunk_id": str(chunk_id),
                            "document_id": str(document_id),
                            "content": "hello world",
                            "position": 1,
                            "chunk_type": "child",
                            "token_count": 8,
                            "page_number": 3,
                            "section": "Section 1.1",
                            "contains_table": True,
                            "parent_chunk_id": str(parent_id),
                            "user_id": str(user_id),
                            "domain": "Legal",
                            "tags": ["contract"],
                            "file_type": "docx",
                            "document_name": "msa.docx",
                        },
                    }
                ]
            }
        }
    )

    results = await repo.search(query="hello", top_k=5)

    chunk = results[0].chunk
    assert chunk.id == chunk_id
    assert chunk.parent_chunk_id == parent_id
    assert chunk.chunk_metadata.page_number == 3
    assert chunk.chunk_metadata.section == "Section 1.1"
    assert chunk.chunk_metadata.contains_table is True
    assert chunk.user_id == user_id
    assert chunk.domain == "Legal"
    assert chunk.tags == ["contract"]
    assert chunk.file_type == "docx"
    assert chunk.document_name == "msa.docx"
