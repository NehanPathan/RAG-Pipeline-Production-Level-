from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class ChunkType(str, Enum):
    PARENT = "parent"
    CHILD = "child"
    TABLE = "table"
    STANDALONE = "standalone"


@dataclass
class DocumentMetadata:
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    domain: str = "general"
    language: str = "en"
    entities: list[dict] = field(default_factory=list)
    custom_metadata: dict = field(default_factory=dict)


@dataclass
class Document:
    file_name: str
    file_type: str
    file_size_bytes: int
    user_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    file_path: str = ""
    status: DocumentStatus = DocumentStatus.PENDING
    error_message: str | None = None
    page_count: int | None = None
    word_count: int | None = None
    loader_used: str | None = None
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    indexed_at: datetime | None = None

    def mark_processing(self) -> None:
        self.status = DocumentStatus.PROCESSING
        self.updated_at = datetime.utcnow()

    def mark_indexed(self) -> None:
        self.status = DocumentStatus.INDEXED
        self.indexed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = DocumentStatus.FAILED
        self.error_message = error
        self.updated_at = datetime.utcnow()


@dataclass
class ChunkMetadata:
    page_number: int | None = None
    section: str | None = None
    contains_table: bool = False
    table_data: dict | None = None


@dataclass
class DocumentChunk:
    document_id: uuid.UUID
    content: str
    position: int
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    parent_chunk_id: uuid.UUID | None = None
    chunk_type: ChunkType = ChunkType.CHILD
    token_count: int = 0
    embedding: list[float] = field(default_factory=list)
    embedding_model: str = ""
    chunk_metadata: ChunkMetadata = field(default_factory=ChunkMetadata)
    qdrant_point_id: uuid.UUID | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    # Denormalized from the parent Document at ingestion time (see
    # IngestionPipeline.ingest) so vector/search repositories can filter on
    # them without a join back to the documents table, and so citations can
    # be built without a repository dependency in the context layer.
    user_id: uuid.UUID | None = None
    domain: str | None = None
    tags: list[str] = field(default_factory=list)
    file_type: str | None = None
    document_name: str | None = None

    @property
    def content_hash(self) -> str:
        import hashlib
        return hashlib.sha256(self.content.encode()).hexdigest()

    def has_embedding(self) -> bool:
        return len(self.embedding) > 0
