from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.domain.entities.document import DocumentChunk


@dataclass
class BM25SearchFilter:
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
    # Governance MAP -- see VectorSearchFilter.sensitivity_in. Both backends
    # must apply the same allow-list or hybrid retrieval would leak through
    # whichever one skipped it.
    sensitivity_in: list[str] | None = None
    # Restrict to current revisions. Defaults off so every existing caller
    # keeps its behaviour; the query pipeline turns it on.
    latest_only: bool = False


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
    async def aggregate_facets(
        self,
        fields: list[str],
        filters: BM25SearchFilter | None = None,
        max_values: int = 50,
    ) -> dict[str, list[tuple[str, int]]]:
        """Distinct values and counts for the given fields, within scope.

        This is the corpus's actual vocabulary. Without it, FilterGenerator
        infers a domain and tags from the wording of a question with no
        knowledge of which values exist -- so one wrong guess ("domain=Sales"
        for an Operations document) makes the whole corpus invisible and the
        system answers "no supporting passage was retrieved" while sitting on
        the document asked about.

        The caller's access filters are applied, so a facet list can never
        reveal that a project or drawing exists that the caller cannot read.
        """
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        ...

    @abstractmethod
    async def create_index_if_not_exists(self) -> None:
        ...
