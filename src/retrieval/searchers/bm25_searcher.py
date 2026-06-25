from __future__ import annotations

import asyncio

from src.domain.repositories.search_repository import (
    BM25ScoredChunk,
    BM25SearchFilter,
    SearchRepository,
)
from src.monitoring.stage_tracer import traced_stage


class BM25Searcher:
    """Searches Elasticsearch for each query variant, merging results across
    variants by keeping the highest bm25_score per chunk.
    """

    def __init__(self, search_repo: SearchRepository) -> None:
        self._search_repo = search_repo

    async def search(
        self,
        queries: list[str],
        top_k: int = 20,
        filters: BM25SearchFilter | None = None,
    ) -> list[BM25ScoredChunk]:
        async with traced_stage("bm25_search", queries=queries, top_k=top_k) as stage:
            results = await self._search(queries, top_k, filters)
            stage.set_result(chunks_found=len(results))
            return results

    async def _search(
        self, queries: list[str], top_k: int, filters: BM25SearchFilter | None
    ) -> list[BM25ScoredChunk]:
        if not queries:
            return []

        per_query_results = await asyncio.gather(
            *(self._search_repo.search(query, top_k=top_k, filters=filters) for query in queries)
        )

        return self._merge(per_query_results, top_k)

    @staticmethod
    def _merge(
        per_query_results: list[list[BM25ScoredChunk]], top_k: int
    ) -> list[BM25ScoredChunk]:
        best_by_chunk: dict[str, BM25ScoredChunk] = {}
        for results in per_query_results:
            for scored in results:
                chunk_id = str(scored.chunk.id)
                existing = best_by_chunk.get(chunk_id)
                if existing is None or scored.bm25_score > existing.bm25_score:
                    best_by_chunk[chunk_id] = scored

        merged = sorted(best_by_chunk.values(), key=lambda s: s.bm25_score, reverse=True)[:top_k]
        for rank, scored in enumerate(merged, start=1):
            scored.rank = rank
        return merged
