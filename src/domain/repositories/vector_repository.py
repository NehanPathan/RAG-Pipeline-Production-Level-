from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.entities.document import DocumentChunk


@dataclass
class VectorSearchFilter:
    user_id: uuid.UUID | None = None
    domain: str | None = None
    tags: list[str] | None = None
    file_type: str | None = None
    document_ids: list[uuid.UUID] | None = None


@dataclass
class ScoredChunk:
    chunk: DocumentChunk
    score: float
    rank: int = 0


class VectorRepository(ABC):
    @abstractmethod
    async def upsert_batch(self, chunks: list[DocumentChunk]) -> None:
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        filters: VectorSearchFilter | None = None,
    ) -> list[ScoredChunk]:
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        ...

    @abstractmethod
    async def get_collection_info(self) -> dict:
        ...

    @abstractmethod
    async def create_collection_if_not_exists(self, vector_size: int) -> None:
        ...
