from __future__ import annotations

import uuid
from typing import Protocol

from src.domain.entities.document import DocumentChunk
from src.ingestion.chunkers.parent_child_chunker import ParentChildChunker
from src.ingestion.parsing.parsed_document import ParsedDocument


class ChunkingStrategy(Protocol):
    """Swap point behind IngestionPipeline (Gap 5,
    docs/architecture/12_phase4a_design_review.md): HybridChunkingPipeline
    (production) and ParentChildOnlyStrategy (Part 8's A/B baseline) both
    satisfy this without IngestionPipeline branching on which one it got."""

    async def chunk(
        self, document_id: uuid.UUID, parsed_document: ParsedDocument
    ) -> list[DocumentChunk]:
        ...


class ParentChildOnlyStrategy:
    """Adapter wrapping the existing `ParentChildChunker.chunk()` unchanged
    -- ignores ParsedDocument's layout/OCR data entirely, operating on
    `parsed_document.raw` exactly as IngestionPipeline used to call it
    directly before Phase 4A. Used only as the "before" baseline in Part 8's
    Hybrid-vs-Current benchmark; HybridChunkingPipeline is what production
    actually uses.
    """

    def __init__(self, parent_child_chunker: ParentChildChunker) -> None:
        self._parent_child_chunker = parent_child_chunker

    async def chunk(
        self, document_id: uuid.UUID, parsed_document: ParsedDocument
    ) -> list[DocumentChunk]:
        return self._parent_child_chunker.chunk(document_id, parsed_document.raw)
