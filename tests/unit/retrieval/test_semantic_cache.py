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


@pytest.mark.asyncio
async def test_lookup_rejects_hit_whose_numbers_differ(cache, repository):
    """The failure this guards against: an embedding barely moves when one
    digit changes, so two factually different questions score above any
    sane similarity threshold and the cache answers one with the other."""
    stale = SemanticCacheEntry(
        query_text="what was revenue in 2023", answer="Revenue in 2023 was 5M."
    )
    repository.find_similar = AsyncMock(return_value=stale)

    assert await cache.lookup("what was revenue in 2024") is None


@pytest.mark.asyncio
async def test_lookup_allows_hit_when_numbers_match(cache, repository):
    entry = SemanticCacheEntry(
        query_text="what was revenue in 2024", answer="Revenue in 2024 was 6M."
    )
    repository.find_similar = AsyncMock(return_value=entry)

    assert await cache.lookup("revenue in 2024, what was it?") is entry


@pytest.mark.asyncio
async def test_lookup_rejects_when_only_one_side_has_a_number(cache, repository):
    """Strict on purpose -- "the refund window" and "the 30 day refund window"
    are not the same question, and treating them as one is the wrong-answer
    direction of the trade."""
    entry = SemanticCacheEntry(query_text="what is the 30 day refund window", answer="30 days.")
    repository.find_similar = AsyncMock(return_value=entry)

    assert await cache.lookup("what is the refund window") is None


@pytest.mark.asyncio
async def test_lookup_ignores_thousands_separators_and_trailing_zeros(cache, repository):
    """Formatting is not a factual difference, so it must not cost a hit."""
    entry = SemanticCacheEntry(query_text="orders above 1,000 units", answer="12 orders.")
    repository.find_similar = AsyncMock(return_value=entry)

    assert await cache.lookup("orders above 1000.0 units") is entry
