from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from src.domain.value_objects.sensitivity import Sensitivity


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

    # MAP -- data classification and lifecycle. The field default is INTERNAL
    # rather than the policy default because a dataclass default cannot read
    # settings; the upload route applies `policy.default_sensitivity`
    # explicitly. Defaulting to INTERNAL (not PUBLIC) means a construction
    # site that forgets to classify fails closed.
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    retention_until: datetime | None = None

    def classify(self, sensitivity: Sensitivity, retention_days: int | None = None) -> None:
        """Set the classification and, optionally, the retention deadline."""
        self.sensitivity = sensitivity
        if retention_days is not None:
            self.retention_until = self.created_at + timedelta(days=retention_days)
        self.updated_at = datetime.utcnow()

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.retention_until is None:
            return False
        return (now or datetime.utcnow()) >= self.retention_until

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
    # Additive Phase 4A fields (Part 5) -- all optional so every existing
    # `ChunkMetadata(...)` construction site and test stays unaffected.
    # `section` (above) is pre-existing free-text; `section_title` is the
    # structured heading text HybridChunkingPipeline's StructureChunker
    # attaches, kept as a distinct field rather than overloading `section`.
    section_title: str | None = None
    heading_level: int | None = None
    semantic_cluster: int | None = None
    ocr_confidence: float | None = None
    language: str | None = None
    # Steel-domain entities found in this chunk's own text (see
    # src/ingestion/extractors/). Their canonical forms are denormalised onto
    # the search payloads as `entity_canonicals`, which is what turns "which
    # chunks mention ISMB 300" into an exact keyword filter rather than a
    # semantic guess.
    entities: list[dict] = field(default_factory=list)


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

    # Denormalized from Document.sensitivity at ingestion time for the same
    # reason as the fields above: retrieval must be able to filter on
    # classification inside Qdrant/Elasticsearch, before any candidate
    # reaches the application, without a join back to `documents`.
    sensitivity: Sensitivity = Sensitivity.INTERNAL

    @property
    def content_hash(self) -> str:
        import hashlib
        return hashlib.sha256(self.content.encode()).hexdigest()

    def has_embedding(self) -> bool:
        return len(self.embedding) > 0
