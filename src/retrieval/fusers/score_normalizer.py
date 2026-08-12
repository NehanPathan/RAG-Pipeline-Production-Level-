from __future__ import annotations

from src.domain.value_objects.retrieval_candidate import FusedChunk


class ScoreNormalizer:
    """Min-max normalizes raw vector/BM25 scores onto [0,1] within a single
    fused result set, purely for human-readable transparency (e.g. in
    /retrieval/inspect). RRF itself only uses ranks, not these values --
    this never feeds back into ranking.
    """

    def normalize(self, fused: list[FusedChunk]) -> list[FusedChunk]:
        self._normalize_field(fused, "vector_score", "normalized_vector_score")
        self._normalize_field(fused, "bm25_score", "normalized_bm25_score")
        return fused

    @staticmethod
    def _normalize_field(fused: list[FusedChunk], raw_attr: str, normalized_attr: str) -> None:
        values = [getattr(c, raw_attr) for c in fused if getattr(c, raw_attr) is not None]
        if not values:
            return

        low, high = min(values), max(values)
        spread = high - low

        for candidate in fused:
            raw = getattr(candidate, raw_attr)
            if raw is None:
                continue
            normalized = 1.0 if spread == 0 else (raw - low) / spread
            setattr(candidate, normalized_attr, normalized)
