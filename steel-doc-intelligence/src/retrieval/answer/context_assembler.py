from __future__ import annotations

import uuid

from src.domain.entities.conversation import Citation
from src.domain.value_objects.context_bundle import AssembledContext, CompressedChunk


class ContextAssembler:
    """Formats the final selected chunks into a single context string with
    inline [n] citation markers matching the indices CitationPreserver
    (Module E) already assigned, so the answer-generation LLM can cite
    using the same numbers PromptBuilder/CitationValidator expect.
    """

    def assemble(
        self, chunks: list[CompressedChunk], citations: dict[uuid.UUID, Citation]
    ) -> AssembledContext:
        blocks = []
        for item in chunks:
            citation = citations.get(item.chunk.id)
            index = citation.index if citation else "?"
            source = citation.document_name if citation else "Unknown source"
            page_suffix = f", p.{citation.page_number}" if citation and citation.page_number else ""
            blocks.append(f"[{index}] ({source}{page_suffix})\n{item.compressed_content}")

        return AssembledContext(
            formatted_text="\n\n".join(blocks),
            total_tokens=sum(item.token_count for item in chunks),
            citations=citations,
        )
