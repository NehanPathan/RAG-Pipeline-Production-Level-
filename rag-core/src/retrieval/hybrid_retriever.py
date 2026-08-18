from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any, TypeVar

from src.domain.repositories.search_repository import BM25ScoredChunk, BM25SearchFilter
from src.domain.repositories.vector_repository import ScoredChunk, VectorSearchFilter
from src.domain.value_objects.retrieval_trace import RetrievalTrace
from src.retrieval.searchers.bm25_searcher import BM25Searcher
from src.retrieval.searchers.vector_searcher import VectorSearcher

T = TypeVar("T")


class HybridRetriever:
    """Runs vector + BM25 retrieval concurrently and assembles a RetrievalTrace.

    Fusion (RRF) is Module C's job, not this class's — this is strictly
    Module B's "run both backends, track what happened" boundary.
    """

    def __init__(self, vector_searcher: VectorSearcher, bm25_searcher: BM25Searcher) -> None:
        self._vector_searcher = vector_searcher
        self._bm25_searcher = bm25_searcher

    async def retrieve(
        self,
        queries: list[str],
        vector_top_k: int,
        bm25_top_k: int,
        vector_filter: VectorSearchFilter | None,
        bm25_filter: BM25SearchFilter | None,
    ) -> tuple[list[ScoredChunk], list[BM25ScoredChunk], RetrievalTrace]:
        (vector_results, vector_ms), (bm25_results, bm25_ms) = await asyncio.gather(
            self._timed(
                self._vector_searcher.search(queries, top_k=vector_top_k, filters=vector_filter)
            ),
            self._timed(
                self._bm25_searcher.search(queries, top_k=bm25_top_k, filters=bm25_filter)
            ),
        )

        trace = RetrievalTrace(
            queries_used=queries,
            vector_count=len(vector_results),
            bm25_count=len(bm25_results),
            vector_search_ms=vector_ms,
            bm25_search_ms=bm25_ms,
        )
        return vector_results, bm25_results, trace

    @staticmethod
    async def _timed(coro: Coroutine[Any, Any, T]) -> tuple[T, int]:
        start = time.perf_counter()
        result = await coro
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return result, elapsed_ms
