from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk


class Reranker(ABC):
    @abstractmethod
    async def rerank(
        self, query: str, candidates: list[FusedChunk], top_n: int
    ) -> list[RerankedChunk]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...
