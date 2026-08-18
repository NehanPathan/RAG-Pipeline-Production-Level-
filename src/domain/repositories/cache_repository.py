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

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        """Invalidate every cached answer derived from the given document.

        Required for deletion to actually mean deletion: without it a removed
        document keeps answering questions from the semantic cache.
        """
        ...
