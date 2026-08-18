from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SimilarityEdge:
    chunk_id_a: uuid.UUID
    chunk_id_b: uuid.UUID
    similarity: float


@dataclass
class LayoutSummary:
    headings: list[dict] = field(default_factory=list)
    outline: list[dict] = field(default_factory=list)
    tables_count: int = 0
    figures_count: int = 0
    lists_count: int = 0
    forms_count: int = 0
    footnotes_count: int = 0


@dataclass
class DocumentIntelligenceSummary:
    """Document-level OCR/layout/embedding summary the Document Intelligence
    UI (Part 7) reads -- deliberately separate from `DocumentChunk`/
    `ChunkMetadata` (per-chunk data already covered there); this is the
    document-wide picture."""

    document_id: uuid.UUID
    ocr_engine: str
    ocr_ran: bool
    ocr_confidence_avg: float | None
    ocr_processing_time_ms: float
    ocr_language: str | None
    embedding_model_chunking: str
    embedding_model_retrieval: str
    layout: LayoutSummary = field(default_factory=LayoutSummary)
    semantic_graph: list[SimilarityEdge] = field(default_factory=list)
    created_at: datetime | None = None
