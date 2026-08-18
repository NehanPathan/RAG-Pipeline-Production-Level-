from __future__ import annotations

from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk
from src.monitoring.stage_tracer import traced_stage
from src.retrieval.fusers.duplicate_remover import DuplicateRemover
from src.retrieval.fusers.rrf_fuser import RRFFusion
from src.retrieval.fusers.score_normalizer import ScoreNormalizer


class Fuser:
    """Orchestrates Module C as one traced stage: RRF -> normalize -> dedup.

    The three steps are individually pure/sync (no I/O), so they don't each
    need their own span -- "fusion" is the retrieval stage that gets traced,
    matching the granularity used for vector_search/bm25_search in Module B.
    """

    def __init__(
        self,
        rrf: RRFFusion,
        normalizer: ScoreNormalizer,
        deduplicator: DuplicateRemover,
    ) -> None:
        self._rrf = rrf
        self._normalizer = normalizer
        self._deduplicator = deduplicator

    async def fuse(
        self,
        vector_results: list[ScoredChunk],
        bm25_results: list[BM25ScoredChunk],
    ) -> list[FusedChunk]:
        async with traced_stage(
            "fusion", vector_count=len(vector_results), bm25_count=len(bm25_results)
        ) as stage:
            fused = self._rrf.fuse(vector_results, bm25_results)
            fused = self._normalizer.normalize(fused)
            fused = self._deduplicator.remove(fused)
            stage.set_result(fused_count=len(fused))
            return fused
