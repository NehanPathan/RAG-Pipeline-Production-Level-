from __future__ import annotations

from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.rerankers.base import Reranker


class PassthroughReranker(Reranker):
    """No-op reranker that returns the top_n fusion results as-is.

    Useful when a local cross-encoder (BGE) is too slow for the available
    hardware (e.g. CPU-only Docker). The RRF score from the fusion stage is
    already a strong signal — skipping the rerank step costs little quality
    but saves 100+ seconds of CPU inference per request.
    """

    @property
    def name(self) -> str:
        return "passthrough"

    async def rerank(
        self, query: str, candidates: list[FusedChunk], top_n: int
    ) -> list[RerankedChunk]:
        top = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)[:top_n]
        return [
            RerankedChunk(fused=chunk, rerank_score=chunk.rrf_score, final_rank=rank)
            for rank, chunk in enumerate(top, start=1)
        ]
