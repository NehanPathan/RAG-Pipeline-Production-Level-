from __future__ import annotations

from src.domain.value_objects.retrieval_candidate import FusedChunk


class DuplicateRemover:
    """Drops exact-duplicate content that surfaced under two different
    chunk_ids (e.g. overlapping parent/child chunks, or boilerplate repeated
    across documents), keeping whichever copy has the higher rrf_score.

    Scoped to exact-content dedup via DocumentChunk.content_hash --
    near-duplicate detection via embedding similarity is a documented
    future enhancement, not built here, to avoid an extra embedding
    comparison pass for marginal benefit at this stage.
    """

    def remove(self, fused: list[FusedChunk]) -> list[FusedChunk]:
        best_by_hash: dict[str, FusedChunk] = {}
        for candidate in fused:
            content_hash = candidate.chunk.content_hash
            existing = best_by_hash.get(content_hash)
            if existing is None or candidate.rrf_score > existing.rrf_score:
                best_by_hash[content_hash] = candidate

        deduped = sorted(best_by_hash.values(), key=lambda c: c.rrf_score, reverse=True)
        for rank, candidate in enumerate(deduped, start=1):
            candidate.rank = rank
        return deduped
