from __future__ import annotations

from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk


class RRFFusion:
    """Reciprocal Rank Fusion: combines vector + BM25 ranked lists into one
    ranked list, keyed by chunk_id, using score(chunk) = sum(1 / (k + rank))
    across whichever list(s) the chunk appears in.

    Deliberately rank-based, not raw-score-based: vector cosine similarity
    and BM25 scores live on incomparable scales, so blending them directly
    would need ad-hoc weighting. RRF sidesteps that by only using each
    list's relative ordering, which is why it reads `.rank` (already
    assigned by VectorSearcher/BM25Searcher), not the raw scores.
    """

    def __init__(self, k: int = 60) -> None:
        self._k = k

    def fuse(
        self,
        vector_results: list[ScoredChunk],
        bm25_results: list[BM25ScoredChunk],
    ) -> list[FusedChunk]:
        candidates: dict[str, FusedChunk] = {}

        for scored in vector_results:
            chunk_id = str(scored.chunk.id)
            candidate = candidates.setdefault(
                chunk_id, FusedChunk(chunk=scored.chunk, rrf_score=0.0)
            )
            candidate.vector_score = scored.score
            candidate.rrf_score += 1.0 / (self._k + scored.rank)

        for scored in bm25_results:
            chunk_id = str(scored.chunk.id)
            candidate = candidates.setdefault(
                chunk_id, FusedChunk(chunk=scored.chunk, rrf_score=0.0)
            )
            candidate.bm25_score = scored.bm25_score
            candidate.rrf_score += 1.0 / (self._k + scored.rank)

        fused = sorted(candidates.values(), key=lambda c: c.rrf_score, reverse=True)
        for rank, candidate in enumerate(fused, start=1):
            candidate.rank = rank
        return fused
