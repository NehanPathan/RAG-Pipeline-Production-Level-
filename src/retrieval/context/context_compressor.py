from __future__ import annotations

import asyncio

from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import RerankedChunk
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

COMPRESS_PROMPT = """\
Given the user's query and a retrieved document excerpt, extract only the \
sentences from the excerpt that are directly relevant to answering the \
query. Preserve the original wording exactly -- do not paraphrase or \
summarize. If nothing in the excerpt is relevant, return the single word: \
NONE.

Query: {query}

Excerpt:
{content}

Relevant excerpt:"""


class ContextCompressor:
    """Uses a small LLM to extract only the query-relevant portion of each
    chunk, reducing token usage before the answer-generation LLM sees the
    context. Falls open (keeps the original content unchanged) on any LLM
    failure, so a flaky compression call degrades context size, not
    correctness. A chunk judged entirely irrelevant ("NONE") is dropped.

    Runs all chunks concurrently via asyncio.gather -- each compression
    call is independent, so there's no reason to pay N sequential LLM
    round-trips for what's already a small, reranked set.
    """

    def __init__(self, llm_provider: LLMProvider, min_chars_to_compress: int = 280) -> None:
        self._llm = llm_provider
        self._min_chars_to_compress = min_chars_to_compress

    async def compress(self, query: str, chunks: list[RerankedChunk]) -> list[CompressedChunk]:
        async with traced_stage(
            "context_compression", query=query, candidates=len(chunks)
        ) as stage:
            results = await asyncio.gather(*(self._compress_one(query, chunk) for chunk in chunks))
            compressed = [c for c in results if c is not None]
            stage.set_result(compressed_count=len(compressed))
            return compressed

    async def _compress_one(self, query: str, chunk: RerankedChunk) -> CompressedChunk | None:
        original = chunk.chunk.content

        # Nothing to trim, so nothing to ask. Compression exists to cut a long
        # passage down to its relevant part; a short chunk has no fat, and the
        # call stops being "extract the relevant sentences" and becomes "judge
        # whether this is relevant at all" -- a different question, which this
        # prompt answers badly.
        #
        # It answers it badly in one direction in particular. A drawing chunk
        # is `DRAWING NO: SSD09.0-01 / REV: A` or `BEAM B-14 ISMB 300 Fe 415`:
        # no sentences, so a prompt asking for "the sentences that are
        # relevant" returns NONE. Every chunk of a CAD sheet was therefore
        # discarded here, retrieval reported seven results, and the grounding
        # gate refused for want of context -- the answer being "I can't answer
        # that from the indexed documents" about a drawing that was indexed,
        # correct, and sitting in the results.
        #
        # The reranker has already scored these, and the token budget still
        # caps the total, so admitting a short chunk costs a few tokens
        # against silently losing every drawing in the corpus.
        if len(original) < self._min_chars_to_compress:
            return CompressedChunk(reranked=chunk, compressed_content=original)

        try:
            response = await self._llm.complete(
                prompt=COMPRESS_PROMPT.format(query=query, content=original),
                max_tokens=400,
                temperature=0.0,
            )
            text = response.strip()
            if text.upper() == "NONE":
                return None
            return CompressedChunk(reranked=chunk, compressed_content=text or original)
        except Exception as e:
            logger.warning("context_compression_failed", chunk_id=str(chunk.chunk.id), error=str(e))
            return CompressedChunk(reranked=chunk, compressed_content=original)
