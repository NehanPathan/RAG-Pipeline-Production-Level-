from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.value_objects.cache_entry import SemanticCacheEntry


class SemanticCacheRepository(ABC):
    @abstractmethod
    async def find_similar(
        self, query_embedding: list[float], score_threshold: float
    ) -> SemanticCacheEntry | None: ...

    @abstractmethod
    async def store(self, query_embedding: list[float], entry: SemanticCacheEntry) -> None: ...

    @abstractmethod
    async def create_collection_if_not_exists(self, vector_size: int) -> None: ...
