from __future__ import annotations

import uuid

from src.domain.entities.conversation import Citation
from src.domain.value_objects.context_bundle import CompressedChunk


class CitationPreserver:
    """Assigns stable [1]..[n] citation indices to the final selected
    chunks, in existing order, and builds the Citation value objects the
    Answer Pipeline (Module G) attaches to generated text.

    Runs *after* TokenBudgetManager/ContextDeduplicator/ContextCompressor so
    indices map exactly to what's actually in the assembled context — a
    citation number must never point at a chunk that didn't make it into
    the prompt.
    """

    def build(self, chunks: list[CompressedChunk]) -> dict[uuid.UUID, Citation]:
        citations: dict[uuid.UUID, Citation] = {}
        for index, item in enumerate(chunks, start=1):
            chunk = item.chunk
            source = chunk.document_name or "Unknown source"
            citations[chunk.id] = Citation(
                index=index,
                source_name=source,
                document_name=source,
                chunk_id=chunk.id,
                page_number=chunk.chunk_metadata.page_number,
                section=chunk.chunk_metadata.section,
                document_id=chunk.document_id,
            )
        return citations
