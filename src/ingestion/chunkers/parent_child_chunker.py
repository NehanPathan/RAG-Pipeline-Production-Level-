from __future__ import annotations

import uuid
from dataclasses import dataclass

import tiktoken

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.ingestion.loaders.base import RawDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ChunkingConfig:
    parent_chunk_size: int = 1024
    child_chunk_size: int = 256
    overlap: int = 32
    encoding_name: str = "cl100k_base"


class ParentChildChunker:
    """
    Splits document into parent chunks (large context) and child chunks (precision retrieval).
    Child chunks store a reference to their parent for context expansion during retrieval.
    """

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()
        self._encoder = tiktoken.get_encoding(self._config.encoding_name)

    def count_tokens(self, text: str) -> int:
        """Exposes the encoder already used internally by chunk()/
        chunk_section() so callers (HybridChunkingPipeline's table chunks)
        don't need their own tiktoken setup."""
        return len(self._encoder.encode(text))

    def chunk(self, document_id: uuid.UUID, raw_document: RawDocument) -> list[DocumentChunk]:
        all_chunks: list[DocumentChunk] = []
        position = 0

        # Process text blocks, keeping page metadata
        full_text_with_meta = self._merge_text_blocks(raw_document)

        # Create parent chunks
        parent_tokens = self._split_into_token_chunks(
            full_text_with_meta,
            self._config.parent_chunk_size,
            self._config.overlap,
        )

        for parent_text, page_num in parent_tokens:
            parent_chunk = DocumentChunk(
                document_id=document_id,
                content=parent_text,
                position=position,
                chunk_type=ChunkType.PARENT,
                token_count=len(self._encoder.encode(parent_text)),
                chunk_metadata=ChunkMetadata(page_number=page_num),
            )
            all_chunks.append(parent_chunk)
            position += 1

            # Create child chunks from each parent
            child_tokens = self._split_into_token_chunks(
                parent_text,
                self._config.child_chunk_size,
                self._config.overlap,
            )
            for child_text, _ in child_tokens:
                if not child_text.strip():
                    continue
                child_chunk = DocumentChunk(
                    document_id=document_id,
                    content=child_text,
                    position=position,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent_chunk.id,
                    token_count=len(self._encoder.encode(child_text)),
                    chunk_metadata=ChunkMetadata(page_number=page_num),
                )
                all_chunks.append(child_chunk)
                position += 1

        # Handle tables as standalone chunks
        for _i, table in enumerate(raw_document.tables):
            if not table.markdown.strip():
                continue
            table_chunk = DocumentChunk(
                document_id=document_id,
                content=f"Table:\n{table.markdown}",
                position=position,
                chunk_type=ChunkType.TABLE,
                token_count=len(self._encoder.encode(table.markdown)),
                chunk_metadata=ChunkMetadata(
                    page_number=table.page_number,
                    contains_table=True,
                ),
            )
            all_chunks.append(table_chunk)
            position += 1

        logger.info(
            "chunking_complete",
            document_id=str(document_id),
            total_chunks=len(all_chunks),
            parent_chunks=sum(1 for c in all_chunks if c.chunk_type == ChunkType.PARENT),
            child_chunks=sum(1 for c in all_chunks if c.chunk_type == ChunkType.CHILD),
            table_chunks=sum(1 for c in all_chunks if c.chunk_type == ChunkType.TABLE),
        )
        return all_chunks

    def chunk_section(
        self,
        document_id: uuid.UUID,
        text: str,
        page_number: int | None = None,
        section_title: str | None = None,
        start_position: int = 0,
    ) -> list[DocumentChunk]:
        """Same parent/child token-window splitting as `chunk()`, scoped to a
        single semantic segment's text instead of the whole document.

        Used by HybridChunkingPipeline (Part 4 Step 3), which calls this once
        per SemanticSegment rather than once for the entire RawDocument --
        `chunk()` itself is unchanged, and this reuses the same private
        `_split_into_token_chunks` helper it already uses internally (Gap 4,
        docs/architecture/12_phase4a_design_review.md).
        """
        chunks: list[DocumentChunk] = []
        position = start_position

        parent_tokens = self._split_into_token_chunks(text, self._config.parent_chunk_size, self._config.overlap)
        for parent_text, _ in parent_tokens:
            if not parent_text.strip():
                continue
            parent_chunk = DocumentChunk(
                document_id=document_id,
                content=parent_text,
                position=position,
                chunk_type=ChunkType.PARENT,
                token_count=len(self._encoder.encode(parent_text)),
                chunk_metadata=ChunkMetadata(page_number=page_number, section_title=section_title),
            )
            chunks.append(parent_chunk)
            position += 1

            child_tokens = self._split_into_token_chunks(
                parent_text, self._config.child_chunk_size, self._config.overlap
            )
            for child_text, _ in child_tokens:
                if not child_text.strip():
                    continue
                child_chunk = DocumentChunk(
                    document_id=document_id,
                    content=child_text,
                    position=position,
                    chunk_type=ChunkType.CHILD,
                    parent_chunk_id=parent_chunk.id,
                    token_count=len(self._encoder.encode(child_text)),
                    chunk_metadata=ChunkMetadata(page_number=page_number, section_title=section_title),
                )
                chunks.append(child_chunk)
                position += 1

        return chunks

    def _merge_text_blocks(self, raw_document: RawDocument) -> tuple[str, int | None]:
        """Merge all text blocks into a single string, return (text, first_page_num)."""
        parts = [block.text for block in raw_document.text_blocks if block.text.strip()]
        first_page = raw_document.text_blocks[0].page_number if raw_document.text_blocks else None
        return "\n\n".join(parts), first_page

    def _split_into_token_chunks(
        self,
        text_or_tuple: str | tuple[str, int | None],
        chunk_size: int,
        overlap: int,
    ) -> list[tuple[str, int | None]]:
        if isinstance(text_or_tuple, tuple):
            text, page_num = text_or_tuple
        else:
            text, page_num = text_or_tuple, None

        tokens = self._encoder.encode(text)
        chunks = []
        start = 0
        while start < len(tokens):
            end = min(start + chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = self._encoder.decode(chunk_tokens)
            chunks.append((chunk_text, page_num))
            if end == len(tokens):
                break
            start = end - overlap

        return chunks
