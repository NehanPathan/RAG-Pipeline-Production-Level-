from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from src.domain.entities.conversation import Citation
from src.domain.value_objects.context_bundle import CompressedChunk
from src.monitoring.logger import get_logger
from src.retrieval.answer.citation_validator import CitationValidator
from src.retrieval.answer.context_assembler import ContextAssembler
from src.retrieval.answer.prompt_builder import PromptBuilder
from src.retrieval.answer.stream_generator import StreamGenerator

logger = get_logger(__name__)


class AnswerPipeline:
    """Orchestrates Module G: context assembly -> prompt construction ->
    streaming generation -> citation validation.

    generate() is itself an async generator yielding SSE-ready event dicts
    (`{"type": "token", "content": ...}` / `{"type": "done", "answer": ...,
    "citations": [...]}` / `{"type": "error", "code": ..., "message": ...}`),
    matching the /chat endpoint's documented event shape (07_api_design.md)
    directly -- the route handler just needs to json.dumps() and forward
    each one. latency_ms is deliberately not included here: this class has
    no visibility into the earlier pipeline stages' timing, so the caller
    (the eventual QueryPipeline/route) is responsible for adding it.
    """

    def __init__(
        self,
        context_assembler: ContextAssembler,
        prompt_builder: PromptBuilder,
        stream_generator: StreamGenerator,
        citation_validator: CitationValidator,
    ) -> None:
        self._context_assembler = context_assembler
        self._prompt_builder = prompt_builder
        self._stream_generator = stream_generator
        self._citation_validator = citation_validator

    @property
    def model_id(self) -> str:
        """The generating model, surfaced for the `provider` metric label and
        for the audit record of which model produced a given answer."""
        return self._stream_generator.model_id

    async def generate(
        self,
        query: str,
        chunks: list[CompressedChunk],
        citations: dict[uuid.UUID, Citation],
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> AsyncIterator[dict]:
        context = self._context_assembler.assemble(chunks, citations)
        prompt = self._prompt_builder.build(query, context)

        tokens: list[str] = []
        try:
            async for token in self._stream_generator.generate(
                prompt, max_tokens=max_tokens, temperature=temperature
            ):
                tokens.append(token)
                yield {"type": "token", "content": token}
        except Exception as e:
            logger.error("answer_generation_failed", error=str(e))
            yield {"type": "error", "code": "generation_failed", "message": str(e)}
            return

        answer = "".join(tokens)
        validated_citations = self._citation_validator.validate(answer, citations)

        yield {
            "type": "done",
            "answer": answer,
            "citations": [self._citation_to_dict(c) for c in validated_citations],
            "model_used": self._stream_generator.model_id,
        }

    @staticmethod
    def _citation_to_dict(citation: Citation) -> dict:
        return {
            "index": citation.index,
            "source_name": citation.source_name,
            "document_name": citation.document_name,
            "chunk_id": str(citation.chunk_id),
            "document_id": str(citation.document_id) if citation.document_id else None,
            "page_number": citation.page_number,
            "section": citation.section,
            "regions": [region.to_dict() for region in citation.regions],
            "region_precision": citation.region_precision,
        }
