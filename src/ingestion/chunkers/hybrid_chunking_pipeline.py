from __future__ import annotations

import uuid

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.parent_child_chunker import ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker, SemanticSegment
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Loaders whose output is a raster image rather than a text layer. Retained
# only as a fallback for ParsedDocuments built outside DocumentParsingService
# and therefore carrying no classification; the real decision is now made
# from content (see `_is_drawing`).
_DRAWING_LOADERS = frozenset({"image_passthrough"})


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
        entity_extractor: RegexSteelEntityExtractor | None = None,
    ) -> None:
        self._structure_chunker = structure_chunker
        self._semantic_chunker = semantic_chunker
        self._parent_child_chunker = parent_child_chunker
        self._validator = validator
        # Only the deterministic pass runs per chunk: it has no I/O and no
        # token cost, and char offsets only mean anything relative to the
        # text they were found in -- which is the chunk, not the document.
        self._entity_extractor = entity_extractor

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
                chunk.chunk_metadata.ocr_confidence = segment.ocr_confidence
            all_chunks.extend(section_chunks)
            position += len(section_chunks)

        self._attach_entities(all_chunks)

        # Stamp provenance quality on every chunk, so a retrieved measurement
        # can be told apart from an OCR'd one at answer time.
        kind = parsed_document.classification.kind.value
        for chunk in all_chunks:
            chunk.chunk_metadata.content_kind = kind

        ocr_metadata = parsed_document.ocr_metadata
        ocr_confidence = ocr_metadata.confidence if ocr_metadata.ran else None
        result = self._validator.validate(
            all_chunks,
            ocr_confidence=ocr_confidence,
            is_drawing=self._is_drawing(parsed_document),
        )

        logger.info(
            "hybrid_chunking_complete",
            document_id=str(document_id),
            sections=len(sections),
            segments=len(segments),
            chunks_valid=len(result.valid),
            chunks_rejected=len(result.rejected),
        )
        return result.valid

    def _attach_entities(self, chunks: list[DocumentChunk]) -> None:
        """Record the steel entities each chunk actually contains.

        Surfaced as `entity_canonicals` on the chunk payload in both search
        backends, which turns "which chunks mention ISMB 300" into an exact
        keyword filter rather than a semantic guess -- and gives BM25 a term
        to match on for a query that is mostly a part number.
        """
        if self._entity_extractor is None:
            return
        for chunk in chunks:
            result = self._entity_extractor.extract_sync(chunk.content)
            if result.entities:
                chunk.chunk_metadata.entities = [e.to_dict() for e in result.entities]

    def _is_drawing(self, parsed_document: ParsedDocument) -> bool:
        """Whether this document should be validated against the drawing OCR floor.

        Engineering drawings scan far worse than prose -- rotated dimension
        text, hatching and leader lines drag the mean down -- so a document
        whose text came from OCR of a drawing gets the lower floor. A scanned
        prose report keeps the standard floor: it is laid out as prose and
        should OCR cleanly, so a low score there is a genuine problem.

        This asks the content classifier rather than the loader name. Keying
        off the loader meant only an uploaded image ever qualified, and a
        drawing exported to PDF -- which is how drawings actually arrive --
        loads through Docling like any report and was held to the prose
        floor. That is the same failure as the original bug: the drawings
        most in need of the lower floor were the ones that never got it.
        """
        if not parsed_document.ocr_metadata.ran:
            return False
        if parsed_document.classification.kind.is_drawing:
            return True
        # Fallback for callers that build a ParsedDocument without running it
        # through DocumentParsingService, which leaves the classification at
        # its default.
        return parsed_document.raw.loader_name in _DRAWING_LOADERS

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
                ocr_confidence=segment.ocr_confidence,
            ),
        )
