import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import BM25ScoredChunk, BM25SearchFilter
from src.domain.repositories.vector_repository import ScoredChunk, VectorSearchFilter
from src.retrieval.hybrid_retriever import HybridRetriever


@pytest.fixture
def vector_searcher():
    searcher = AsyncMock()
    searcher.search = AsyncMock(
        return_value=[
            ScoredChunk(
                chunk=DocumentChunk(document_id=uuid.uuid4(), content="v", position=0), score=0.9
            )
        ]
    )
    return searcher


@pytest.fixture
def bm25_searcher():
    searcher = AsyncMock()
    searcher.search = AsyncMock(
        return_value=[
            BM25ScoredChunk(
                chunk=DocumentChunk(document_id=uuid.uuid4(), content="b", position=0),
                bm25_score=10.0,
            ),
            BM25ScoredChunk(
                chunk=DocumentChunk(document_id=uuid.uuid4(), content="b2", position=0),
                bm25_score=8.0,
            ),
        ]
    )
    return searcher


@pytest.fixture
def retriever(vector_searcher, bm25_searcher):
    return HybridRetriever(vector_searcher=vector_searcher, bm25_searcher=bm25_searcher)


@pytest.mark.asyncio
async def test_retrieve_runs_both_searchers_and_builds_trace(retriever):
    vector_results, bm25_results, trace = await retriever.retrieve(
        queries=["q1", "q2"],
        vector_top_k=20,
        bm25_top_k=20,
        vector_filter=None,
        bm25_filter=None,
    )

    assert len(vector_results) == 1
    assert len(bm25_results) == 2
    assert trace.queries_used == ["q1", "q2"]
    assert trace.vector_count == 1
    assert trace.bm25_count == 2
    assert trace.vector_search_ms >= 0
    assert trace.bm25_search_ms >= 0


@pytest.mark.asyncio
async def test_retrieve_passes_top_k_and_filters_to_each_searcher(retriever, vector_searcher, bm25_searcher):
    vector_filter = VectorSearchFilter(domain="HR")
    bm25_filter = BM25SearchFilter(domain="HR")

    await retriever.retrieve(
        queries=["q1"],
        vector_top_k=15,
        bm25_top_k=25,
        vector_filter=vector_filter,
        bm25_filter=bm25_filter,
    )

    vector_searcher.search.assert_called_once_with(["q1"], top_k=15, filters=vector_filter)
    bm25_searcher.search.assert_called_once_with(["q1"], top_k=25, filters=bm25_filter)
