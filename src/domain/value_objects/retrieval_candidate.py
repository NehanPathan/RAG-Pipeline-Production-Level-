from __future__ import annotations

from dataclasses import dataclass

from src.domain.entities.document import DocumentChunk


@dataclass
class FusedChunk:
    """Output of RRF fusion: one chunk plus every score that contributed to
    its combined rank, for both ranking (rrf_score) and debug transparency
    (vector_score/bm25_score, and their normalized 0-1 counterparts).
    """

    chunk: DocumentChunk
    rrf_score: float
    rank: int = 0
    vector_score: float | None = None
    bm25_score: float | None = None
    normalized_vector_score: float | None = None
    normalized_bm25_score: float | None = None


@dataclass
class RerankedChunk:
    """Output of the Reranking Layer (Module D): a FusedChunk plus a
    cross-encoder/API relevance score and the final post-rerank rank.
    """

    fused: FusedChunk
    rerank_score: float
    final_rank: int = 0

    @property
    def chunk(self) -> DocumentChunk:
        return self.fused.chunk
