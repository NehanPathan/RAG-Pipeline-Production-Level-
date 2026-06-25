from __future__ import annotations

from typing import Any

from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.monitoring.stage_tracer import traced_stage
from src.retrieval.rerankers.base import Reranker


class CohereReranker(Reranker):
    """Cohere Rerank API (cohere.AsyncClientV2.rerank). The client is
    injected so unit tests substitute a fake instead of making real API
    calls. Cohere returns results already sorted by relevance and truncated
    to top_n, so unlike BGEReranker there's no client-side sort/slice step.
    """

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def name(self) -> str:
        return self._model

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

        documents = [candidate.chunk.content for candidate in candidates]
        response = await self._client.rerank(
            model=self._model, query=query, documents=documents, top_n=top_n
        )

        reranked = [
            RerankedChunk(fused=candidates[result.index], rerank_score=result.relevance_score)
            for result in response.results
        ]
        for rank, item in enumerate(reranked, start=1):
            item.final_rank = rank
        return reranked
