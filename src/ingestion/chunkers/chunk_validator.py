from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.domain.entities.document import DocumentChunk

_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


@dataclass
class ValidationResult:
    valid: list[DocumentChunk] = field(default_factory=list)
    rejected: list[tuple[DocumentChunk, str]] = field(default_factory=list)


class ChunkValidator:
    """Part 4, Step 4: rejects chunks that are too small, duplicated, mostly
    whitespace, OCR garbage, or below an OCR confidence floor -- the last
    step of HybridChunkingPipeline, run after parent/child splitting.

    Rejection reasons are returned alongside the rejected chunk (not just a
    pass/fail count) so the Document Intelligence UI (Part 7) and the
    benchmark script (Part 8) can show *why* a chunk was dropped.
    """

    def __init__(
        self,
        min_chars: int = 10,
        max_whitespace_ratio: float = 0.6,
        min_alnum_ratio: float = 0.35,
        min_ocr_confidence: float = 0.35,
    ) -> None:
        self._min_chars = min_chars
        self._max_whitespace_ratio = max_whitespace_ratio
        self._min_alnum_ratio = min_alnum_ratio
        self._min_ocr_confidence = min_ocr_confidence

    def validate(
        self, chunks: list[DocumentChunk], ocr_confidence: float | None = None
    ) -> ValidationResult:
        result = ValidationResult()
        seen_hashes: set[str] = set()

        for chunk in chunks:
            reason = self._reject_reason(chunk, ocr_confidence, seen_hashes)
            if reason is None:
                seen_hashes.add(chunk.content_hash)
                result.valid.append(chunk)
            else:
                result.rejected.append((chunk, reason))

        return result

    def _reject_reason(
        self, chunk: DocumentChunk, ocr_confidence: float | None, seen_hashes: set[str]
    ) -> str | None:
        content = chunk.content
        stripped = content.strip()

        if not stripped:
            return "whitespace_only"

        if len(stripped) < self._min_chars:
            return "too_small"

        if chunk.content_hash in seen_hashes:
            return "duplicate"

        non_whitespace = len("".join(stripped.split()))
        whitespace_ratio = 1 - (non_whitespace / len(stripped))
        if whitespace_ratio > self._max_whitespace_ratio:
            return "whitespace_only"

        alnum_count = len(_ALNUM_RE.findall(stripped))
        if (alnum_count / len(stripped)) < self._min_alnum_ratio:
            return "ocr_garbage"

        if ocr_confidence is not None and ocr_confidence < self._min_ocr_confidence:
            return "low_ocr_confidence"

        return None
