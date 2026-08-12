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

        # Deduplication is scoped *per chunk type*, not across all chunks.
        #
        # Parent/child splitting produces children whose text is a substring of
        # their parent -- and when a section is short enough to fit one child
        # window, the child's text is byte-identical to the parent's. A single
        # shared hash set therefore saw the parent first and rejected its only
        # child as a "duplicate".
        #
        # That was silently fatal: parents are never embedded (see
        # IngestionPipeline, which embeds only child/table/standalone chunks),
        # so any document short enough to fit one child window ended up with
        # zero vectors and was invisible to semantic search. Keyword search
        # still found the parent, which is why the document looked indexed.
        # Found by an end-to-end test on a 184-character policy document.
        #
        # A child duplicating another *child* is still a genuine duplicate and
        # is still rejected.
        seen_by_type: dict[str, set[str]] = {}

        for chunk in chunks:
            seen = seen_by_type.setdefault(chunk.chunk_type.value, set())
            reason = self._reject_reason(chunk, ocr_confidence, seen)
            if reason is None:
                seen.add(chunk.content_hash)
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
