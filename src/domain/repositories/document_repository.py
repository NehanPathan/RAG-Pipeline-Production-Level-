from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from src.domain.entities.document import Document, DocumentChunk, DocumentStatus


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
    ) -> tuple[list[Document], int]:
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
    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        ...
