from unittest.mock import AsyncMock

import pytest

from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.retrieval.cache.semantic_cache import SemanticCache


@pytest.fixture
def repository():
    return AsyncMock()


@pytest.fixture
def cache(repository, mock_embedding_provider):
    return SemanticCache(
        repository=repository, embedding_provider=mock_embedding_provider, score_threshold=0.9
    )


@pytest.mark.asyncio
async def test_lookup_returns_entry_on_hit(cache, repository, mock_embedding_provider):
    expected = SemanticCacheEntry(query_text="q", answer="a")
    repository.find_similar = AsyncMock(return_value=expected)

    result = await cache.lookup("what is the refund policy")

    assert result is expected
    mock_embedding_provider.embed_query.assert_called_once_with("what is the refund policy")
    repository.find_similar.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_passes_configured_score_threshold(cache, repository):
    repository.find_similar = AsyncMock(return_value=None)

    await cache.lookup("query")

    call = repository.find_similar.call_args
    threshold = call.args[1] if len(call.args) > 1 else call.kwargs.get("score_threshold")
    assert threshold == 0.9


@pytest.mark.asyncio
async def test_lookup_returns_none_on_miss(cache, repository):
    repository.find_similar = AsyncMock(return_value=None)

    result = await cache.lookup("query")

    assert result is None


@pytest.mark.asyncio
async def test_store_embeds_query_and_calls_repository(cache, repository, mock_embedding_provider):
    repository.store = AsyncMock()

    await cache.store("query", "answer", [{"index": 1}], "gpt-4o")

    mock_embedding_provider.embed_query.assert_called_once_with("query")
    repository.store.assert_called_once()
    call = repository.store.call_args
    entry = call.args[1] if len(call.args) > 1 else call.kwargs["entry"]
    assert entry.query_text == "query"
    assert entry.answer == "answer"
    assert entry.model_used == "gpt-4o"
