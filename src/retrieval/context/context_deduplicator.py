from __future__ import annotations

from src.domain.value_objects.retrieval_candidate import RerankedChunk


class ContextDeduplicator:
    """Drops chunks whose content is fully contained within another,
    higher-ranked chunk already kept (e.g. a child chunk wholly contained in
    its parent's text, or overlapping chunks from chunk-overlap windows).

    Distinct from Module C's DuplicateRemover, which only catches *exact*
    content-hash matches across the much larger pre-rerank candidate pool.
    This runs on the small, already-ranked set right before compression/
    prompt assembly, where the goal is "don't spend the limited context
    window on text the LLM already has," not "don't waste a reranker call
    on a literal duplicate."

    Containment is checked one-directionally (is this chunk's content inside
    an already-kept chunk's content), so it only catches a later, smaller
    chunk being redundant relative to an earlier, larger one — not the
    reverse, since a larger chunk arriving after a smaller one still adds
    genuinely new surrounding context.
    """

    def deduplicate(self, chunks: list[RerankedChunk]) -> list[RerankedChunk]:
        kept: list[RerankedChunk] = []
        kept_contents: list[str] = []

        for candidate in chunks:
            content = candidate.chunk.content
            if any(content in kept_content for kept_content in kept_contents):
                continue
            kept.append(candidate)
            kept_contents.append(content)

        return kept
