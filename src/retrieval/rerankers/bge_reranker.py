from __future__ import annotations

import asyncio
from typing import Any

from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.monitoring.stage_tracer import traced_stage
from src.retrieval.rerankers.base import Reranker


class BGEReranker(Reranker):
    """Local cross-encoder reranking via sentence-transformers.

    The CrossEncoder instance is injected rather than constructed here, so
    unit tests substitute a fake with the same `.predict()` surface instead
    of downloading a real multi-GB model from HuggingFace Hub.
    `CrossEncoder.predict()` is a synchronous, CPU/GPU-bound call — run via
    `asyncio.to_thread` so it doesn't block the event loop.
    """

    def __init__(self, cross_encoder: Any, model_name: str) -> None:
        self._cross_encoder = cross_encoder
        self._model_name = model_name

    @property
    def name(self) -> str:
        return self._model_name

    async def rerank(
        self, query: str, candidates: list[FusedChunk], top_n: int
    ) -> list[RerankedChunk]:
        async with traced_stage(
            "rerank", reranker=self.name, candidates=len(candidates), top_n=top_n
        ) as stage:
            reranked = await self._rerank(query, candidates, top_n)
            stage.set_result(reranked_count=len(reranked))
            return reranked

    async def _rerank(
        self, query: str, candidates: list[FusedChunk], top_n: int
    ) -> list[RerankedChunk]:
        if not candidates:
            return []

        pairs = [(query, candidate.chunk.content) for candidate in candidates]
        scores = await asyncio.to_thread(self._cross_encoder.predict, pairs)

        scored = [
            RerankedChunk(fused=candidate, rerank_score=float(score))
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        scored.sort(key=lambda r: r.rerank_score, reverse=True)

        top = scored[:top_n]
        for rank, item in enumerate(top, start=1):
            item.final_rank = rank
        return top
