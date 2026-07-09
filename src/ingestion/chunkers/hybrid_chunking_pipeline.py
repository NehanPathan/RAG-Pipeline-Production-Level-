from __future__ import annotations

import uuid

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.parent_child_chunker import ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker, SemanticSegment
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class HybridChunkingPipeline:
    """Part 4: StructureChunker -> SemanticChunker -> ParentChildChunker ->
    ChunkValidator.

    Does not replace ParentChildChunker -- reuses it unchanged via
    `chunk_section()` (Gap 4, docs/architecture/12_phase4a_design_review.md)
    as the final token-windowing step, after structure- and semantic-aware
    splitting have already produced better segment boundaries than "split
    every N tokens" alone would. This is the `ChunkingStrategy` used in
    production; `ParentChildOnlyStrategy` (chunking_strategy.py) is the A/B
    baseline for Part 8's benchmark.
    """

    def __init__(
        self,
        structure_chunker: StructureChunker,
        semantic_chunker: SemanticChunker,
        parent_child_chunker: ParentChildChunker,
        validator: ChunkValidator,
    ) -> None:
        self._structure_chunker = structure_chunker
        self._semantic_chunker = semantic_chunker
        self._parent_child_chunker = parent_child_chunker
        self._validator = validator

    async def chunk(self, document_id: uuid.UUID, parsed_document: ParsedDocument) -> list[DocumentChunk]:
        sections = self._structure_chunker.split(parsed_document)
        segments = await self._semantic_chunker.split(sections)

        all_chunks: list[DocumentChunk] = []
        position = 0
        for segment in segments:
            if segment.is_table:
                all_chunks.append(self._table_chunk(document_id, segment, position))
                position += 1
                continue

            section_chunks = self._parent_child_chunker.chunk_section(
                document_id,
                segment.text,
                page_number=segment.page_number,
                section_title=segment.section_title,
                start_position=position,
            )
            for chunk in section_chunks:
                chunk.chunk_metadata.heading_level = segment.heading_level
            all_chunks.extend(section_chunks)
            position += len(section_chunks)

        ocr_metadata = parsed_document.ocr_metadata
        ocr_confidence = ocr_metadata.confidence if ocr_metadata.ran else None
        result = self._validator.validate(all_chunks, ocr_confidence=ocr_confidence)

        logger.info(
            "hybrid_chunking_complete",
            document_id=str(document_id),
            sections=len(sections),
            segments=len(segments),
            chunks_valid=len(result.valid),
            chunks_rejected=len(result.rejected),
        )
        return result.valid

    def _table_chunk(self, document_id: uuid.UUID, segment: SemanticSegment, position: int) -> DocumentChunk:
        content = f"Table:\n{segment.text}"
        return DocumentChunk(
            document_id=document_id,
            content=content,
            position=position,
            chunk_type=ChunkType.TABLE,
            token_count=self._parent_child_chunker.count_tokens(content),
            chunk_metadata=ChunkMetadata(
                page_number=segment.page_number,
                section_title=segment.section_title,
                heading_level=segment.heading_level,
                contains_table=True,
            ),
        )
