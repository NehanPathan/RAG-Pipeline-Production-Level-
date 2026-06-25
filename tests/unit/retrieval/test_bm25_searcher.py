import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import BM25ScoredChunk
from src.retrieval.searchers.bm25_searcher import BM25Searcher


def _scored(chunk_id, score):
    return BM25ScoredChunk(
        chunk=DocumentChunk(id=chunk_id, document_id=uuid.uuid4(), content="c", position=0),
        bm25_score=score,
    )


@pytest.fixture
def search_repo():
    return AsyncMock()


@pytest.fixture
def searcher(search_repo):
    return BM25Searcher(search_repo=search_repo)


@pytest.mark.asyncio
async def test_search_queries_backend_once_per_variant(searcher, search_repo):
    search_repo.search = AsyncMock(return_value=[])

    await searcher.search(["q1", "q2"], top_k=10)

    assert search_repo.search.call_count == 2


@pytest.mark.asyncio
async def test_search_keeps_highest_score_for_duplicate_chunk(searcher, search_repo):
    chunk_id = uuid.uuid4()
    search_repo.search = AsyncMock(
        side_effect=[[_scored(chunk_id, 5.0)], [_scored(chunk_id, 12.0)]]
    )

    results = await searcher.search(["q1", "q2"], top_k=10)

    assert len(results) == 1
    assert results[0].bm25_score == 12.0


@pytest.mark.asyncio
async def test_search_truncates_to_top_k_and_assigns_ranks(searcher, search_repo):
    search_repo.search = AsyncMock(
        side_effect=[
            [_scored(uuid.uuid4(), 10.0), _scored(uuid.uuid4(), 5.0)],
            [_scored(uuid.uuid4(), 1.0)],
        ]
    )

    results = await searcher.search(["q1", "q2"], top_k=2)

    assert len(results) == 2
    assert [r.bm25_score for r in results] == [10.0, 5.0]
    assert [r.rank for r in results] == [1, 2]


@pytest.mark.asyncio
async def test_search_returns_empty_list_for_no_queries(searcher, search_repo):
    results = await searcher.search([], top_k=10)

    assert results == []
    search_repo.search.assert_not_called()
