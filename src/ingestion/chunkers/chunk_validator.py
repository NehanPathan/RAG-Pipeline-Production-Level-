from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.domain.entities.document import ChunkType, DocumentChunk

_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

# Chunk types whose text is legitimately sparse in alphanumerics. A schedule
# or BOM row window is mostly pipes, spaces and empty cells --
# `| M1 |  |  | 2 |` scores about 0.15 on the alnum ratio and was being
# discarded as OCR garbage. Structured tabular content is validated on its
# other signals instead.
_SPARSE_ALNUM_TYPES = frozenset({ChunkType.TABLE})

# ContentKind values that earn the lower OCR floor. Compared as strings
# rather than importing the enum: the validator is a leaf and has no other
# reason to depend on the parsing package. Kept in sync by
# test_drawing_floor_covers_every_drawing_content_kind.
_DRAWING_CONTENT_KINDS = frozenset(
    {"vector_drawing", "scanned_drawing", "mixed", "cad_native"}
)


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
        min_ocr_confidence_drawing: float = 0.15,
    ) -> None:
        self._min_chars = min_chars
        self._max_whitespace_ratio = max_whitespace_ratio
        self._min_alnum_ratio = min_alnum_ratio
        self._min_ocr_confidence = min_ocr_confidence
        self._min_ocr_confidence_drawing = min_ocr_confidence_drawing

    def validate(
        self,
        chunks: list[DocumentChunk],
        ocr_confidence: float | None = None,
        is_drawing: bool = False,
    ) -> ValidationResult:
        """Validate chunks, using each chunk's own OCR confidence when it has one.

        `ocr_confidence` is the *document* average and is only a fallback for
        chunks that carry no per-chunk figure. Comparing every chunk against
        the document mean was actively harmful: engineering drawings OCR at
        0.30-0.50 on average because of rotated dimension text, hatching and
        leader lines, so a single sub-threshold average rejected *every*
        chunk in the document -- and the document still reported
        `status=indexed` with `chunks_created=0`. Silent, total data loss on
        precisely the input class this system exists to handle.

        `is_drawing` selects the lower floor, because a drawing's legible
        title block and schedule are worth keeping even when the sheet as a
        whole scans badly.
        """
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
            reason = self._reject_reason(chunk, ocr_confidence, seen, is_drawing)
            if reason is None:
                seen.add(chunk.content_hash)
                result.valid.append(chunk)
            else:
                result.rejected.append((chunk, reason))

        return result

    def _reject_reason(
        self,
        chunk: DocumentChunk,
        ocr_confidence: float | None,
        seen_hashes: set[str],
        is_drawing: bool = False,
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

        if chunk.chunk_type not in _SPARSE_ALNUM_TYPES:
            alnum_count = len(_ALNUM_RE.findall(stripped))
            if (alnum_count / len(stripped)) < self._min_alnum_ratio:
                return "ocr_garbage"

        # Per-chunk confidence when the chunk has one; the document average
        # only as a fallback. See validate() for why the document average
        # must never be the primary signal.
        effective_confidence = (
            chunk.chunk_metadata.ocr_confidence
            if chunk.chunk_metadata.ocr_confidence is not None
            else ocr_confidence
        )
        floor = (
            self._min_ocr_confidence_drawing
            if self._chunk_is_drawing(chunk, is_drawing)
            else self._min_ocr_confidence
        )
        if effective_confidence is not None and effective_confidence < floor:
            return "low_ocr_confidence"

        return None

    @staticmethod
    def _chunk_is_drawing(chunk: DocumentChunk, document_is_drawing: bool) -> bool:
        """Which floor this chunk answers to.

        The chunk's own `content_kind` wins when it has one. A single PDF
        routinely mixes a specification, a schedule and a plotted sheet, and
        a document-wide flag would either hold the drawing pages to the prose
        floor or wave the prose pages through on the drawing floor. The
        argument remains the fallback for chunks that were never classified.
        """
        kind = chunk.chunk_metadata.content_kind
        if kind is None:
            return document_is_drawing
        return kind in _DRAWING_CONTENT_KINDS
