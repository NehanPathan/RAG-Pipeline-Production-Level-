from __future__ import annotations

import asyncio

from src.domain.repositories.vector_repository import (
    ScoredChunk,
    VectorRepository,
    VectorSearchFilter,
)
from src.ingestion.embedders.base import EmbeddingProvider
from src.monitoring.stage_tracer import traced_stage


class VectorSearcher:
    """Embeds each query variant and searches Qdrant, merging results across
    variants by keeping the highest score per chunk (the same chunk can
    surface from multiple rewritten/expanded query variants).
    """

    def __init__(self, vector_repo: VectorRepository, embedding_provider: EmbeddingProvider) -> None:
        self._vector_repo = vector_repo
        self._embedding_provider = embedding_provider

    async def search(
        self,
        queries: list[str],
        top_k: int = 20,
        filters: VectorSearchFilter | None = None,
    ) -> list[ScoredChunk]:
        async with traced_stage("vector_search", queries=queries, top_k=top_k) as stage:
            results = await self._search(queries, top_k, filters)
            stage.set_result(chunks_found=len(results))
            return results

    async def _search(
        self, queries: list[str], top_k: int, filters: VectorSearchFilter | None
    ) -> list[ScoredChunk]:
        if not queries:
            return []

        embeddings = await self._embedding_provider.embed_texts(queries)

        per_query_results = await asyncio.gather(
            *(
                self._vector_repo.search(embedding, top_k=top_k, filters=filters)
                for embedding in embeddings
            )
        )

        return self._merge(per_query_results, top_k)

    @staticmethod
    def _merge(per_query_results: list[list[ScoredChunk]], top_k: int) -> list[ScoredChunk]:
        best_by_chunk: dict[str, ScoredChunk] = {}
        for results in per_query_results:
            for scored in results:
                chunk_id = str(scored.chunk.id)
                existing = best_by_chunk.get(chunk_id)
                if existing is None or scored.score > existing.score:
                    best_by_chunk[chunk_id] = scored

        merged = sorted(best_by_chunk.values(), key=lambda s: s.score, reverse=True)[:top_k]
        for rank, scored in enumerate(merged, start=1):
            scored.rank = rank
        return merged
