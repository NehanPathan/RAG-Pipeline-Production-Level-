import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.retrieval.searchers.vector_searcher import VectorSearcher


def _scored(chunk_id, score):
    return ScoredChunk(
        chunk=DocumentChunk(id=chunk_id, document_id=uuid.uuid4(), content="c", position=0),
        score=score,
    )


@pytest.fixture
def vector_repo():
    return AsyncMock()


@pytest.fixture
def embedding_provider():
    provider = AsyncMock()
    provider.embed_texts = AsyncMock(side_effect=lambda texts: [[0.1] * 3 for _ in texts])
    return provider


@pytest.fixture
def searcher(vector_repo, embedding_provider):
    return VectorSearcher(vector_repo=vector_repo, embedding_provider=embedding_provider)


@pytest.mark.asyncio
async def test_search_embeds_all_query_variants_and_searches_each(searcher, embedding_provider, vector_repo):
    vector_repo.search = AsyncMock(return_value=[])

    await searcher.search(["q1", "q2"], top_k=10)

    embedding_provider.embed_texts.assert_called_once_with(["q1", "q2"])
    assert vector_repo.search.call_count == 2


@pytest.mark.asyncio
async def test_search_keeps_highest_score_for_duplicate_chunk(searcher, vector_repo):
    chunk_id = uuid.uuid4()
    vector_repo.search = AsyncMock(
        side_effect=[
            [_scored(chunk_id, 0.5)],
            [_scored(chunk_id, 0.9)],
        ]
    )

    results = await searcher.search(["q1", "q2"], top_k=10)

    assert len(results) == 1
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_search_truncates_to_top_k_after_merge(searcher, vector_repo):
    vector_repo.search = AsyncMock(
        side_effect=[
            [_scored(uuid.uuid4(), 0.9), _scored(uuid.uuid4(), 0.8)],
            [_scored(uuid.uuid4(), 0.7)],
        ]
    )

    results = await searcher.search(["q1", "q2"], top_k=2)

    assert len(results) == 2
    assert [r.score for r in results] == [0.9, 0.8]


@pytest.mark.asyncio
async def test_search_assigns_sequential_ranks_by_descending_score(searcher, vector_repo):
    vector_repo.search = AsyncMock(
        side_effect=[[_scored(uuid.uuid4(), 0.3), _scored(uuid.uuid4(), 0.9)], []]
    )

    results = await searcher.search(["q1", "q2"], top_k=10)

    assert [r.rank for r in results] == [1, 2]
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_search_returns_empty_list_for_no_queries(searcher, vector_repo, embedding_provider):
    results = await searcher.search([], top_k=10)

    assert results == []
    embedding_provider.embed_texts.assert_not_called()
    vector_repo.search.assert_not_called()
