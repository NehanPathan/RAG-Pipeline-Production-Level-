"""Tells an engineering drawing from prose, and a scan from a native PDF.

Most steel drawings do not arrive as DXF. They arrive as PDFs -- plotted
from CAD, or scanned from a plan chest -- and before this the pipeline
treated all three as the same thing: prose.

Measured on real fixtures, the three cases separate cleanly:

                        native text   words/page   periods/100 chars
  vector CAD PDF             60           60             0.34
  scanned drawing PDF         0           60             0.35
  prose specification       219          219             1.83

Two independent signals, each answering a different question:

* **Is there a native text layer?** A PDF plotted from CAD carries its text
  as text. A scan carries pixels. This is the only reliable way to tell them
  apart, because Docling silently OCRs scanned pages and hands back text
  that looks exactly like extracted text -- 60 words either way, same block
  count, same labels. Without this check the pipeline cannot know whether
  what it is holding came from the document or from an OCR engine, and
  therefore cannot know how much to trust it.

* **Does the text read like sentences?** Prose terminates sentences; a
  drawing is labels, dimensions and schedule rows. The period density
  differs by 5x, which is a wider margin than any threshold needs.

What this deliberately does *not* claim: a PDF drawing is not a DXF. There
are no layers, no block references, and no exact measured values -- a
dimension on a plotted sheet is the string the drafter's CAD system
rendered, and on a scan it is whatever OCR made of that string. The DXF path
reads geometry; this path reads a picture of geometry. Anything downstream
that needs certainty about a dimension should prefer the CAD source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.ingestion.loaders.base import RawDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class ContentKind(str, Enum):
    """What kind of document this is, for routing purposes."""

    PROSE = "prose"
    #: Plotted from CAD. Text is real text with real coordinates.
    VECTOR_DRAWING = "vector_drawing"
    #: A picture of a drawing. Every character came from OCR.
    SCANNED_DRAWING = "scanned_drawing"
    #: A scanned report or specification -- pixels, but prose.
    SCANNED_PROSE = "scanned_prose"
    #: Read from a DXF/DWG. Layers, blocks and exact measurements available.
    CAD_NATIVE = "cad_native"

    @property
    def is_drawing(self) -> bool:
        return self in (
            ContentKind.VECTOR_DRAWING,
            ContentKind.SCANNED_DRAWING,
            ContentKind.CAD_NATIVE,
        )

    @property
    def is_scanned(self) -> bool:
        return self in (ContentKind.SCANNED_DRAWING, ContentKind.SCANNED_PROSE)

    @property
    def has_exact_dimensions(self) -> bool:
        """Whether a measurement can be trusted as the CAD value.

        True only for DXF/DWG, where DIMENSION entities carry the measured
        number. On a plotted PDF it is a rendered string; on a scan it is an
        OCR guess at a rendered string.
        """
        return self is ContentKind.CAD_NATIVE


@dataclass(frozen=True)
class ContentClassification:
    kind: ContentKind
    reason: str
    words_per_page: float = 0.0
    sentence_density: float = 0.0
    native_text_words: int = 0
    title_block_markers: int = 0
    #: True when the file carries its own text layer, False when every
    #: character must come from OCR, None when the question does not apply
    #: (DXF, DOCX). `None` is not "no" -- it is what the OCR detector reads to
    #: mean "I have no opinion, use your existing rules".
    has_native_text_layer: bool | None = None

    def as_dict(self) -> dict[str, str | float | int | bool | None]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "words_per_page": round(self.words_per_page, 1),
            "sentence_density": round(self.sentence_density, 2),
            "native_text_words": self.native_text_words,
            "title_block_markers": self.title_block_markers,
            "has_native_text_layer": self.has_native_text_layer,
        }


# Title-block keys. Their presence is close to conclusive: prose does not
# say "DRAWING NO" and "SCALE 1:100" in passing.
_TITLE_BLOCK = re.compile(
    r"\b(DRAWING\s*N[O0]|DWG\s*N[O0]|DRG\s*N[O0]|SHEET\s*N[O0]|REV(?:ISION)?\b"
    r"|SCALE\s*[:=]?\s*1\s*:\s*\d|DRAWN\s*BY|CHECKED\s*BY|JOB\s*N[O0])",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]")

# Thresholds. The measured gap between drawings and prose is roughly 5x on
# both signals, so these sit in the middle of a wide valley rather than being
# tuned to a particular corpus.
MAX_DRAWING_SENTENCE_DENSITY = 1.0  # periods per 100 characters
MAX_DRAWING_WORDS_PER_PAGE = 150.0
# Below this, there is no meaningful text layer at all: a handful of stray
# characters from a stamp or a footer does not make a scan a native PDF.
MIN_NATIVE_TEXT_WORDS = 10


class DrawingContentDetector:
    def __init__(
        self,
        max_sentence_density: float = MAX_DRAWING_SENTENCE_DENSITY,
        max_words_per_page: float = MAX_DRAWING_WORDS_PER_PAGE,
        min_native_words: int = MIN_NATIVE_TEXT_WORDS,
    ) -> None:
        self._max_sentence_density = max_sentence_density
        self._max_words_per_page = max_words_per_page
        self._min_native_words = min_native_words

    def classify(
        self, raw_document: RawDocument, file_path: Path, file_type: str
    ) -> ContentClassification:
        ext = file_type.lower().lstrip(".")

        if ext in ("dxf", "dwg"):
            return ContentClassification(
                kind=ContentKind.CAD_NATIVE,
                reason="read from CAD: layers, blocks and exact dimensions available",
            )

        text = raw_document.full_text
        words = len(text.split())
        pages = max(raw_document.page_count or 1, 1)
        words_per_page = words / pages
        density = len(_SENTENCE_END.findall(text)) / max(len(text), 1) * 100
        markers = len(_TITLE_BLOCK.findall(text))

        if ext in ("png", "jpg", "jpeg", "tiff", "tif", "bmp"):
            # An uploaded image of a drawing. Nothing native to check.
            kind = (
                ContentKind.SCANNED_DRAWING
                if self._looks_like_drawing(words_per_page, density, markers)
                else ContentKind.SCANNED_PROSE
            )
            return ContentClassification(
                kind=kind,
                reason="image file: all text is OCR-derived",
                words_per_page=words_per_page,
                sentence_density=density,
                native_text_words=0,
                title_block_markers=markers,
                has_native_text_layer=False,
            )

        if ext != "pdf":
            return ContentClassification(
                kind=ContentKind.PROSE,
                reason=f"'{ext}' is a text format",
                words_per_page=words_per_page,
                sentence_density=density,
            )

        native_words = self._native_text_words(file_path)
        drawing = self._looks_like_drawing(words_per_page, density, markers)
        # None when the probe could not run at all. Unmeasurable is not the
        # same claim as "no text layer": the second forces a full OCR pass,
        # and inferring it from a missing or unreadable file would make an
        # operational error look like a scan and silently double the OCR
        # cost of every document. Left as None, the OCR detector's existing
        # density rules simply apply -- which is what happened before this
        # signal existed.
        scanned = None if native_words is None else native_words < self._min_native_words

        if drawing and scanned:
            kind, reason = (
                ContentKind.SCANNED_DRAWING,
                f"no native text layer ({native_words} words) and drawing-like content",
            )
        elif scanned:
            kind, reason = (
                ContentKind.SCANNED_PROSE,
                f"no native text layer ({native_words} words), prose-like content",
            )
        elif drawing:
            kind, reason = (
                ContentKind.VECTOR_DRAWING,
                f"drawing-like content, text layer {self._layer_phrase(native_words)}",
            )
        else:
            kind, reason = (
                ContentKind.PROSE,
                f"prose-like content, text layer {self._layer_phrase(native_words)}",
            )

        classification = ContentClassification(
            kind=kind,
            reason=reason,
            words_per_page=words_per_page,
            sentence_density=density,
            native_text_words=native_words or 0,
            title_block_markers=markers,
            has_native_text_layer=None if scanned is None else not scanned,
        )
        logger.info("content_classified", file=file_path.name, **classification.as_dict())
        return classification

    def _looks_like_drawing(
        self, words_per_page: float, sentence_density: float, markers: int
    ) -> bool:
        """Title block wins outright; otherwise both statistical signals must agree.

        Requiring both is deliberate. A short prose page trips the
        words-per-page test on its own, and a dense table of contents trips
        the sentence-density test on its own; neither is a drawing.
        """
        if markers >= 2:
            return True
        return (
            sentence_density < self._max_sentence_density
            and words_per_page < self._max_words_per_page
        )

    @staticmethod
    def _layer_phrase(native_words: int | None) -> str:
        return "unreadable" if native_words is None else f"has {native_words} words"

    def _native_text_words(self, file_path: Path) -> int | None:
        """Words in the PDF's own text layer, or None if it could not be read.

        Read with pypdf rather than through the loader on purpose: by the
        time Docling has finished, an OCR'd scan is indistinguishable from a
        plotted sheet. This is the only point at which the difference is
        still visible.

        `0` and `None` are deliberately different answers -- "I read the file
        and there is no text" versus "I could not read the file". Only the
        first is evidence of a scan.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            text = "".join((page.extract_text() or "") for page in reader.pages)
            return len(text.split())
        except Exception as exc:
            logger.warning("native_text_probe_failed", file=file_path.name, error=str(exc))
            return None
