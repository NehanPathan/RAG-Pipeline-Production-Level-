from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import AsyncQdrantClient

from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.infrastructure.vector_store.qdrant.cache_repository import QdrantSemanticCacheRepository


@pytest.fixture
def mock_client():
    return AsyncMock(spec=AsyncQdrantClient)


@pytest.fixture
def repo(mock_client):
    return QdrantSemanticCacheRepository(client=mock_client, collection_name="semantic_query_cache")


@pytest.mark.asyncio
async def test_find_similar_returns_entry_on_hit(repo, mock_client):
    hit = MagicMock(
        payload={
            "query_text": "what is the refund policy",
            "answer": "30 days",
            "citations": [{"index": 1}],
            "model_used": "gpt-4o",
        }
    )
    mock_client.query_points = AsyncMock(return_value=MagicMock(points=[hit]))

    entry = await repo.find_similar(query_embedding=[0.1, 0.2], score_threshold=0.95)

    assert entry is not None
    assert entry.query_text == "what is the refund policy"
    assert entry.answer == "30 days"
    assert entry.citations == [{"index": 1}]
    assert entry.model_used == "gpt-4o"


@pytest.mark.asyncio
async def test_find_similar_returns_none_on_miss(repo, mock_client):
    mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))

    entry = await repo.find_similar(query_embedding=[0.1, 0.2], score_threshold=0.95)

    assert entry is None


@pytest.mark.asyncio
async def test_find_similar_passes_score_threshold(repo, mock_client):
    mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))

    await repo.find_similar(query_embedding=[0.1, 0.2], score_threshold=0.9)

    mock_client.query_points.assert_called_once_with(
        collection_name="semantic_query_cache",
        query=[0.1, 0.2],
        limit=1,
        score_threshold=0.9,
        with_payload=True,
    )


@pytest.mark.asyncio
async def test_store_upserts_entry_with_embedding_as_vector(repo, mock_client):
    entry = SemanticCacheEntry(query_text="q", answer="a", citations=[], model_used="gpt-4o")

    await repo.store(query_embedding=[0.3, 0.4], entry=entry)

    mock_client.upsert.assert_called_once()
    point = mock_client.upsert.call_args.kwargs["points"][0]
    assert point.vector == [0.3, 0.4]
    assert point.payload["query_text"] == "q"
    assert point.payload["answer"] == "a"
    assert point.payload["model_used"] == "gpt-4o"


@pytest.mark.asyncio
async def test_create_collection_creates_when_missing(repo, mock_client):
    mock_client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))

    await repo.create_collection_if_not_exists(vector_size=1536)

    mock_client.create_collection.assert_called_once()


@pytest.mark.asyncio
async def test_create_collection_skips_when_already_exists(repo, mock_client):
    existing = MagicMock()
    existing.name = "semantic_query_cache"
    mock_client.get_collections = AsyncMock(return_value=MagicMock(collections=[existing]))

    await repo.create_collection_if_not_exists(vector_size=1536)

    mock_client.create_collection.assert_not_called()
