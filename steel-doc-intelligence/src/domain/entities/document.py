from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from src.domain.value_objects.provenance import Region
from src.domain.value_objects.sensitivity import Sensitivity


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    # Screened out before indexing: stored and readable by a steward, but
    # never chunked, embedded or retrievable. Distinct from FAILED, which
    # means "we could not read it" rather than "we read it and it does not
    # belong here".
    QUARANTINED = "quarantined"


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
    entities: list[dict[str, Any]] = field(default_factory=list)
    custom_metadata: dict[str, Any] = field(default_factory=dict)


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

    # Project scope. None means personal: readable by `user_id` alone.
    project_id: uuid.UUID | None = None

    # Revision identity. All optional -- a specification is a document
    # without being a revision of a drawing.
    drawing_id: uuid.UUID | None = None
    revision_label: str | None = None
    revision_index: int | None = None
    revision_date: datetime | None = None
    revision_note: str | None = None
    is_latest: bool = True
    superseded_by_document_id: uuid.UUID | None = None
    superseded_at: datetime | None = None

    def supersede(self, by_document_id: uuid.UUID, when: datetime | None = None) -> None:
        """Mark this revision as no longer current.

        Recording *which* document replaced it, not merely that something
        did, is what lets the UI offer "superseded by Rev C" instead of a
        dead end -- and what lets a reader who followed an old citation find
        the current sheet.
        """
        self.is_latest = False
        self.superseded_by_document_id = by_document_id
        self.superseded_at = when or datetime.utcnow()
        self.updated_at = datetime.utcnow()

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

    def mark_quarantined(self, reason: str) -> None:
        """Screened out of the corpus, with the reason kept.

        Not deleted and not failed. The file stays where it is and a steward
        can read the reason and disagree -- a false quarantine has to be
        recoverable, because the alternative is a user whose legitimate
        drawing vanished with no explanation.
        """
        self.status = DocumentStatus.QUARANTINED
        self.error_message = reason
        self.updated_at = datetime.utcnow()


@dataclass
class ChunkMetadata:
    page_number: int | None = None
    section: str | None = None
    contains_table: bool = False
    table_data: dict[str, Any] | None = None
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
    entities: list[dict[str, Any]] = field(default_factory=list)
    # How this chunk's text was obtained: "prose", "vector_drawing",
    # "scanned_drawing", "scanned_prose" or "cad_native". Carried through to
    # both search backends because it is the difference between a dimension
    # that is the CAD value and one that is OCR's reading of a plotted
    # string -- an answer quoting a measurement should be able to say which.
    # See src/ingestion/parsing/drawing_detector.py.
    content_kind: str | None = None
    # Where on the sheet this chunk's text sits, so a citation resolves to a
    # rectangle rather than to a whole drawing. A list because a chunk spans
    # several blocks and the box enclosing all of them would cover the gaps
    # between as well. See src/domain/value_objects/provenance.py.
    regions: list[Region] = field(default_factory=list)
    # How precisely: "block" when the regions came from the blocks' own
    # boxes, "section" when inherited from a parent whose text was split
    # further, "page" when only the page is known. Stated rather than implied
    # so a viewer can draw a tight box or a soft one instead of pretending to
    # a precision it does not have.
    region_precision: str | None = None
    # CAD layers this chunk's content came from. Denormalised onto both
    # search payloads as an indexed keyword list, which is what turns
    # "what is on S-BOLTS" into a filter instead of a similarity guess --
    # and a drawing question is structural far more often than it is
    # semantic. Empty for prose, which has no layers.
    layers: list[str] = field(default_factory=list)


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

    # Project and revision identity, denormalized for the same reason as the
    # fields above: retrieval filters on them inside Qdrant/Elasticsearch,
    # before any candidate reaches the application.
    project_id: uuid.UUID | None = None
    project_number: str | None = None
    drawing_id: uuid.UUID | None = None
    drawing_number: str | None = None
    revision_label: str | None = None
    # Current revision of its drawing. True for documents that are not
    # revisions of anything, which is the correct reading: nothing supersedes
    # them.
    is_latest: bool = True

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
