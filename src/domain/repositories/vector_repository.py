from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.domain.entities.document import DocumentChunk


@dataclass
class VectorSearchFilter:
    user_id: uuid.UUID | None = None
    # Reachability, read as a disjunction with `user_id`: a chunk matches if
    # the caller owns it OR it belongs to one of these projects. Both halves
    # are set together from the Principal (see
    # MetadataFilterSpec.apply_access_scope).
    project_ids: list[uuid.UUID] | None = None
    domain: str | None = None
    tags: list[str] | None = None
    file_type: str | None = None
    document_ids: list[uuid.UUID] | None = None
    # Governance MAP: the classification allow-list the caller's clearance
    # permits, as raw strings. Applied as a pre-filter inside the vector
    # store so over-classified chunks never enter the candidate set and
    # cannot consume top_k slots from chunks the caller may actually read.
    sensitivity_in: list[str] | None = None
    # Restrict to current revisions. Defaults off so every existing caller
    # keeps its behaviour; the query pipeline turns it on.
    latest_only: bool = False


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
    async def set_payload_by_document(
        self, document_id: uuid.UUID, payload: dict[str, Any]
    ) -> None:
        """Update selected payload keys on every vector of one document.

        Distinct from `upsert_batch` because callers that only change
        metadata (reclassification, revision supersession) do not hold the
        embeddings and must not be forced to recompute them.
        """
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
