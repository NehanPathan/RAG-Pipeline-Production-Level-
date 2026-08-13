from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from src.domain.entities.document import Document, DocumentChunk, DocumentStatus
from src.domain.value_objects.sensitivity import Sensitivity


class DocumentRepository(ABC):
    @abstractmethod
    async def save(self, document: Document) -> Document:
        ...

    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        ...

    @abstractmethod
    async def list_by_user(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        size: int = 20,
        status: DocumentStatus | None = None,
        domain: str | None = None,
        file_type: str | None = None,
        sensitivity_in: list[str] | None = None,
        search: str | None = None,
        project_ids: list[uuid.UUID] | None = None,
    ) -> tuple[list[Document], int]:
        """Page through a user's documents.

        `sensitivity_in` is the caller's clearance allow-list and is applied
        **inside the query**, before both the count and the LIMIT/OFFSET.
        Filtering the page after it comes back would still disclose an
        accurate total of documents the caller may not read, and would return
        short pages whose length leaks how many were withheld.

        `search` matches the file name case-insensitively and, like the
        clearance filter, is applied inside the query -- filtering a page
        after fetching it would return pages shorter than `size` and make
        `total` meaningless.
        """
        ...

    @abstractmethod
    async def update(self, document: Document) -> Document:
        ...

    @abstractmethod
    async def delete(self, document_id: uuid.UUID) -> bool:
        ...


class ChunkRepository(ABC):
    @abstractmethod
    async def save_batch(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        ...

    @abstractmethod
    async def get_by_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        ...

    @abstractmethod
    async def get_by_ids(self, chunk_ids: list[uuid.UUID]) -> list[DocumentChunk]:
        ...

    @abstractmethod
    async def set_sensitivity(self, document_id: uuid.UUID, sensitivity: Sensitivity) -> int:
        """Reclassify every chunk of a document, returning the row count."""
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        ...
