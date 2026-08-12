from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.domain.entities.document import DocumentChunk


@dataclass
class BM25SearchFilter:
    user_id: uuid.UUID | None = None
    domain: str | None = None
    tags: list[str] | None = None
    file_type: str | None = None
    document_ids: list[uuid.UUID] | None = None
    # Governance MAP -- see VectorSearchFilter.sensitivity_in. Both backends
    # must apply the same allow-list or hybrid retrieval would leak through
    # whichever one skipped it.
    sensitivity_in: list[str] | None = None


@dataclass
class BM25ScoredChunk:
    chunk: DocumentChunk
    bm25_score: float
    rank: int = 0


class SearchRepository(ABC):
    @abstractmethod
    async def index_batch(self, chunks: list[DocumentChunk]) -> None:
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 20,
        filters: BM25SearchFilter | None = None,
    ) -> list[BM25ScoredChunk]:
        ...

    @abstractmethod
    async def update_fields_by_document(
        self, document_id: uuid.UUID, fields: dict[str, Any]
    ) -> int:
        """Update selected fields on every chunk of one document.

        Distinct from `index_batch` because a full re-index rewrites every
        field, including the ones denormalized at ingest that a caller
        reloading from Postgres cannot reconstruct.
        """
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        ...

    @abstractmethod
    async def create_index_if_not_exists(self) -> None:
        ...
