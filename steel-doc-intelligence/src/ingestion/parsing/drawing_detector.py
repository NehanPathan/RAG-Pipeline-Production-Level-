"""Tells engineering drawings from prose, and scans from native PDFs -- per page.

Most steel drawings do not arrive as DXF. They arrive as PDFs, and a single
client PDF is routinely not one thing: a specification page, a beam
schedule, a plotted CAD sheet, a scan of an old sheet, and a detail page
with a drawing on top and erection notes underneath. Classifying the
document as a whole averages all of that into one meaningless verdict.

Measured on the reference five-page fixture, the document-level answer was
"vector drawing" at 111 words/page and 1.34 periods per 100 characters --
mid-range on both signals, wrong for four of the five pages, and, worst, it
summed the native text layer to 458 words and so could not see that page 4
had none. The scan was invisible.

Per page, the same fixture separates cleanly:

  page  native text   docling words   kind
    1       210            210        prose
    2        58             58        prose (a schedule table)
    3        29             29        vector drawing
    4         0             30        scanned drawing
    5       164            164        mixed

Everything here is deterministic: regex, arithmetic, and one pypdf read of
the file. **No LLM or network call is made, per page or otherwise** -- a
per-page model call would cost more than the ingestion it serves and would
make classification non-reproducible. Signals used:

* **Native text layer, per page.** A page plotted from CAD carries its text
  as text; a scanned page carries pixels. This is the only reliable way to
  tell them apart, because Docling silently OCRs scanned pages with its
  internal RapidOCR and hands back text that looks exactly like extracted
  text. Without this the pipeline cannot know whether what it holds came
  from the document or from an OCR engine, and so cannot know how far to
  trust it.
* **Sentence density.** Prose terminates sentences; a drawing is labels,
  dimensions and schedule rows.
* **Word density.** A drawing sheet is mostly geometry.
* **Title-block markers.** `DRAWING NO`, `SCALE 1:100`, `REV` -- prose does
  not say these in passing.
* **Dimension and callout patterns.** `400x400x20`, `M24`, `Ø20`, `1:50`.
* **Images and their coverage.** Distinguishes a scanned page from a blank
  one, both of which have no text.

Each page gets a **confidence**, and a page whose signals genuinely conflict
can be referred to an optional `VisionPageClassifier`. None ships by
default, so the default path stays free and offline.

Blocks are classified individually, so a page with a drawing above and notes
below is `MIXED` rather than being forced into one type, and the chunks cut
from each half are labelled for what they actually are.

What this deliberately does *not* claim: a PDF drawing is not a DXF. There
are no layers, no block references and no exact measured values -- a
dimension on a plotted sheet is the string the drafter's CAD system
rendered, and on a scan it is whatever OCR made of it. The DXF path reads
geometry; this path reads a picture of geometry.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from src.ingestion.loaders.base import RawDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class ContentKind(str, Enum):
    """What a page (or document) is, for routing purposes."""

    PROSE = "prose"
    #: Plotted from CAD. Text is real text with real coordinates.
    VECTOR_DRAWING = "vector_drawing"
    #: A picture of a drawing. Every character came from OCR.
    SCANNED_DRAWING = "scanned_drawing"
    #: A scanned report or specification -- pixels, but prose.
    SCANNED_PROSE = "scanned_prose"
    #: Drawing and prose on the same page, both substantial.
    MIXED = "mixed"
    #: Read from a DXF/DWG. Layers, blocks and exact measurements available.
    CAD_NATIVE = "cad_native"

    @property
    def is_drawing(self) -> bool:
        """Whether drawing content is present -- not whether it is all drawing.

        MIXED counts. A mixed page's title block scans exactly as badly as a
        pure drawing's, and dropping it would lose the drawing number.
        """
        return self in (
            ContentKind.VECTOR_DRAWING,
            ContentKind.SCANNED_DRAWING,
            ContentKind.MIXED,
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


class VisionPageClassifier(Protocol):
    """Optional last resort for a page whose deterministic signals conflict.

    Never called unless a page's confidence falls below the detector's
    threshold, and never called at all unless an implementation is injected
    -- which none is by default. A per-page model call on every ingested
    document would cost more than the rest of the pipeline combined and
    would make classification irreproducible between runs.
    """

    async def classify_page(self, file_path: Path, page_number: int) -> ContentKind | None: ...


@dataclass(frozen=True)
class PageSignals:
    """The deterministic evidence for one page. All cheap to compute."""

    words: int = 0
    native_text_words: int | None = None
    sentence_density: float = 0.0
    title_block_markers: int = 0
    dimension_markers: int = 0
    image_count: int = 0
    image_coverage: float = 0.0
    has_table: bool = False
    drawing_block_ratio: float = 0.0
    drawing_words: int = 0
    prose_words: int = 0

    @property
    def has_native_text_layer(self) -> bool | None:
        """None means the probe could not run, which is not the same as "no"."""
        if self.native_text_words is None:
            return None
        return self.native_text_words >= MIN_NATIVE_TEXT_WORDS


@dataclass(frozen=True)
class PageClassification:
    page_number: int
    kind: ContentKind
    confidence: float
    reason: str
    signals: PageSignals = field(default_factory=PageSignals)

    def as_dict(self) -> dict[str, object]:
        return {
            "page": self.page_number,
            "kind": self.kind.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "words": self.signals.words,
            "native_text_words": self.signals.native_text_words,
            "sentence_density": round(self.signals.sentence_density, 2),
            "title_block_markers": self.signals.title_block_markers,
            "drawing_block_ratio": round(self.signals.drawing_block_ratio, 2),
        }


@dataclass(frozen=True)
class ContentClassification:
    """A document's classification: a summary plus the per-page detail.

    `kind` is the summary and is MIXED whenever the pages disagree. Nothing
    downstream should route on it alone for a multi-page document -- use
    `kind_for_page`, which is what chunks are stamped from.
    """

    kind: ContentKind
    reason: str
    pages: dict[int, PageClassification] = field(default_factory=dict)
    #: Index into `RawDocument.text_blocks` -> whether that block reads as
    #: drawing content. Lets a chunk be labelled by what it actually holds
    #: rather than by the page it sits on.
    drawing_blocks: frozenset[int] = frozenset()
    confidence: float = 1.0

    def kind_for_page(self, page_number: int | None) -> ContentKind:
        """The kind for one page, falling back to the document summary."""
        if page_number is None:
            return self.kind
        page = self.pages.get(page_number)
        return page.kind if page else self.kind

    def confidence_for_page(self, page_number: int | None) -> float:
        if page_number is None:
            return self.confidence
        page = self.pages.get(page_number)
        return page.confidence if page else self.confidence

    @property
    def has_native_text_layer(self) -> bool | None:
        """Document-wide answer, for the single-page and non-PDF cases.

        None when pages disagree or the probe could not run -- a document
        with one scanned page among natives has no single honest answer, and
        `pages_without_text_layer` is what the OCR detector reads instead.
        """
        answers = {p.signals.has_native_text_layer for p in self.pages.values()}
        if len(answers) != 1:
            return None
        return answers.pop()

    @property
    def pages_without_text_layer(self) -> list[int]:
        """Pages whose text, if any, can only have come from an OCR engine."""
        return sorted(
            number
            for number, page in self.pages.items()
            if page.signals.has_native_text_layer is False
        )

    @property
    def is_uniform(self) -> bool:
        return len({p.kind for p in self.pages.values()}) <= 1

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
            "pages": {n: p.kind.value for n, p in sorted(self.pages.items())},
        }


# Title-block keys. Their presence is close to conclusive: prose does not
# say "DRAWING NO" and "SCALE 1:100" in passing.
_TITLE_BLOCK = re.compile(
    r"\b(DRAWING\s*N[O0]|DWG\s*N[O0]|DRG\s*N[O0]|SHEET\s*N[O0]|REV(?:ISION)?\b"
    r"|SCALE\s*[:=]?\s*1\s*:\s*\d|DRAWN\s*BY|CHECKED\s*BY|JOB\s*N[O0])",
    re.IGNORECASE,
)

# Callouts as they are written on a sheet: plate sizes, bolt designations,
# diameters, scales, and bare millimetre dimensions. Prose that discusses a
# bolt writes "property class 8.8 bolts"; a drawing writes "M24".
_DIMENSION_MARKER = re.compile(
    r"(\b\d{2,4}\s*[xX×]\s*\d{2,4}(\s*[xX×]\s*\d{1,3})?\b"  # noqa: RUF001 - 400x400x20
    r"|\bM\d{1,2}\s*[xX×]?\s*\d{0,3}\b"  # noqa: RUF001 - M20, M20x60
    r"|[ØøΦ⌀]\s*\d+"  # diameters
    r"|\b1\s*:\s*\d{1,4}\b"  # scales
    r"|\b\d{3,5}\s*(MM|mm)\b)"  # 6000 mm
)

_SENTENCE_END = re.compile(r"[.!?]")

# Thresholds. The measured gap between drawings and prose is roughly 5x on
# both statistical signals, so these sit in the middle of a wide valley
# rather than being tuned to a particular corpus.
MAX_DRAWING_SENTENCE_DENSITY = 1.0  # periods per 100 characters
MAX_DRAWING_WORDS_PER_PAGE = 150.0
# Below this, there is no meaningful text layer at all: a few stray
# characters from a stamp or a footer do not make a scan a native PDF.
MIN_NATIVE_TEXT_WORDS = 10
# A page is MIXED when both content types are substantially present -- which
# is not the same as their being balanced. A drawing's text is inherently
# sparse (labels, dimensions, a title block), so weighing the two halves by
# word count systematically under-counts the drawing side: the reference
# fixture's genuinely-mixed page scores 0.15 on that ratio and would be
# filed as a pure drawing. Presence of each, above its own floor, is the
# honest test.
MIN_DRAWING_WORDS_FOR_MIXED = 5
MIN_PROSE_WORDS_FOR_MIXED = 40
# Below this the deterministic signals genuinely conflict, and a vision
# fallback earns its cost -- if one is configured.
AMBIGUOUS_CONFIDENCE = 0.34

# How much each signal counts toward the drawing-vs-prose decision. A title
# block is worth more than any statistic because it is nearly unambiguous;
# raw word count is worth least because a short page is short for many
# reasons.
_WEIGHT_TITLE_BLOCK = 2.0
_WEIGHT_SENTENCE_DENSITY = 1.5
_WEIGHT_DIMENSIONS = 1.0
_WEIGHT_WORD_DENSITY = 1.0


class DrawingContentDetector:
    def __init__(
        self,
        max_sentence_density: float = MAX_DRAWING_SENTENCE_DENSITY,
        max_words_per_page: float = MAX_DRAWING_WORDS_PER_PAGE,
        min_native_words: int = MIN_NATIVE_TEXT_WORDS,
        vision_classifier: VisionPageClassifier | None = None,
        ambiguous_confidence: float = AMBIGUOUS_CONFIDENCE,
    ) -> None:
        self._max_sentence_density = max_sentence_density
        self._max_words_per_page = max_words_per_page
        self._min_native_words = min_native_words
        self._vision_classifier = vision_classifier
        self._ambiguous_confidence = ambiguous_confidence

    # -- entry points --------------------------------------------------

    def classify(
        self, raw_document: RawDocument, file_path: Path, file_type: str
    ) -> ContentClassification:
        ext = file_type.lower().lstrip(".")

        if ext in ("dxf", "dwg"):
            return ContentClassification(
                kind=ContentKind.CAD_NATIVE,
                reason="read from CAD: layers, blocks and exact dimensions available",
            )

        if ext in _NEVER_SCANNED_EXTENSIONS:
            return ContentClassification(
                kind=ContentKind.PROSE, reason=f"'{ext}' is a text format", confidence=1.0
            )

        is_image = ext in _IMAGE_EXTENSIONS
        native_by_page = None if is_image else self._native_words_by_page(file_path)
        images_by_page = None if is_image else self._images_by_page(file_path)

        drawing_blocks = self._classify_blocks(raw_document)
        page_numbers = self._page_numbers(raw_document, native_by_page)

        pages: dict[int, PageClassification] = {}
        for number in page_numbers:
            pages[number] = self._classify_page(
                raw_document,
                number,
                drawing_blocks,
                native_words=(
                    0
                    if is_image
                    else (native_by_page or {}).get(number)
                    if native_by_page
                    else None
                ),
                image_info=(1, 1.0) if is_image else (images_by_page or {}).get(number, (0, 0.0)),
            )

        classification = self._summarize(pages, drawing_blocks)
        logger.info("content_classified", file=file_path.name, **classification.as_dict())
        return classification

    async def classify_with_fallback(
        self, raw_document: RawDocument, file_path: Path, file_type: str
    ) -> ContentClassification:
        """`classify`, then ask the vision classifier about ambiguous pages only.

        Returns the deterministic result unchanged when no classifier is
        configured, which is the default. Pages that the deterministic
        signals resolve confidently are never sent anywhere.
        """
        classification = self.classify(raw_document, file_path, file_type)
        if self._vision_classifier is None or not classification.pages:
            return classification

        ambiguous = [
            page
            for page in classification.pages.values()
            if page.confidence < self._ambiguous_confidence
        ]
        if not ambiguous:
            return classification

        logger.info(
            "vision_fallback_invoked",
            file=file_path.name,
            pages=[p.page_number for p in ambiguous],
            of_total=len(classification.pages),
        )
        resolved = dict(classification.pages)
        for page in ambiguous:
            try:
                kind = await self._vision_classifier.classify_page(file_path, page.page_number)
            except Exception as exc:
                # A fallback that fails must not fail the ingestion: the
                # deterministic verdict is still a verdict.
                logger.warning("vision_fallback_failed", page=page.page_number, error=str(exc))
                continue
            if kind is None:
                continue
            resolved[page.page_number] = PageClassification(
                page_number=page.page_number,
                kind=self._keep_scanned_axis(kind, page),
                confidence=self._ambiguous_confidence,
                reason=f"vision fallback (deterministic signals conflicted: {page.reason})",
                signals=page.signals,
            )
        return self._summarize(resolved, classification.drawing_blocks)

    # -- per-page ------------------------------------------------------

    def _classify_page(
        self,
        raw_document: RawDocument,
        page_number: int,
        drawing_blocks: frozenset[int],
        native_words: int | None,
        image_info: tuple[int, float],
    ) -> PageClassification:
        text = self._page_text(raw_document, page_number)
        words = len(text.split())
        density = len(_SENTENCE_END.findall(text)) / max(len(text), 1) * 100
        markers = len(_TITLE_BLOCK.findall(text))
        dimensions = len(_DIMENSION_MARKER.findall(text))
        image_count, image_coverage = image_info
        has_table = any(t.page_number == page_number for t in raw_document.tables)
        drawing_words, prose_words = self._split_words(raw_document, page_number, drawing_blocks)
        total_words = drawing_words + prose_words
        ratio = drawing_words / total_words if total_words else 0.0

        signals = PageSignals(
            words=words,
            native_text_words=native_words,
            sentence_density=density,
            title_block_markers=markers,
            dimension_markers=dimensions,
            image_count=image_count,
            image_coverage=image_coverage,
            has_table=has_table,
            drawing_block_ratio=ratio,
            drawing_words=drawing_words,
            prose_words=prose_words,
        )

        drawing_score, prose_score = self._score(signals)
        total = drawing_score + prose_score
        confidence = abs(drawing_score - prose_score) / total if total else 0.0

        # Both kinds substantially present: the page is neither, and forcing
        # it into one would mislabel every chunk cut from the other half.
        # Drawing evidence has to be corroborated by a title block or
        # dimension callouts, or any sparse caption beside a paragraph would
        # make the page "mixed".
        mixed = (
            drawing_words >= MIN_DRAWING_WORDS_FOR_MIXED
            and prose_words >= MIN_PROSE_WORDS_FOR_MIXED
            and (markers >= 1 or dimensions >= 2)
        )
        # No text layer, but something is on the page: a scan. An image is
        # what separates that from a genuinely blank page.
        scanned = (
            native_words is not None
            and native_words < self._min_native_words
            and (words > 0 or image_count > 0)
        )

        if mixed:
            kind = ContentKind.MIXED
            # Confidence in *being mixed*, not in the drawing-vs-prose
            # margin -- which is near zero here by definition, since the page
            # really is both. Reporting that margin would send every
            # correctly-identified mixed page to the ambiguity fallback.
            confidence = min(
                min(drawing_words / MIN_DRAWING_WORDS_FOR_MIXED, 2.0) / 2.0,
                min(prose_words / MIN_PROSE_WORDS_FOR_MIXED, 2.0) / 2.0,
            )
            reason = (
                f"drawing and prose both substantial "
                f"({drawing_words} drawing words, {prose_words} prose words, "
                f"{markers} title-block markers)"
            )
        elif drawing_score > prose_score:
            kind = ContentKind.SCANNED_DRAWING if scanned else ContentKind.VECTOR_DRAWING
            reason = self._reason(signals, "drawing", scanned)
        else:
            kind = ContentKind.SCANNED_PROSE if scanned else ContentKind.PROSE
            reason = self._reason(signals, "prose", scanned)

        return PageClassification(
            page_number=page_number,
            kind=kind,
            confidence=round(confidence, 3),
            reason=reason,
            signals=signals,
        )

    def _score(self, signals: PageSignals) -> tuple[float, float]:
        """Weighted votes for drawing and for prose.

        Each signal votes independently so that one strong indicator cannot
        be silently cancelled by an absent one, and so the margin between
        the two totals is a usable confidence.
        """
        drawing = 0.0
        prose = 0.0

        if signals.title_block_markers >= 2:
            drawing += _WEIGHT_TITLE_BLOCK
        elif signals.title_block_markers == 1:
            drawing += _WEIGHT_TITLE_BLOCK / 2
        else:
            prose += _WEIGHT_TITLE_BLOCK / 2

        if signals.sentence_density < self._max_sentence_density:
            drawing += _WEIGHT_SENTENCE_DENSITY
        else:
            prose += _WEIGHT_SENTENCE_DENSITY

        if signals.dimension_markers >= 2:
            drawing += _WEIGHT_DIMENSIONS
        else:
            prose += _WEIGHT_DIMENSIONS / 2

        if signals.words < self._max_words_per_page:
            drawing += _WEIGHT_WORD_DENSITY
        else:
            prose += _WEIGHT_WORD_DENSITY

        # A page the loader resolved into a table grid is close to
        # conclusive, and weighted accordingly: a schedule has no terminated
        # sentences and few words, so it trips both statistical
        # drawing signals at once and would otherwise tie. On the reference
        # fixture the table page scored exactly 2.5 against 2.5 -- the right
        # verdict at zero confidence, which is no verdict at all.
        #
        # Note this is a different axis from `ChunkType.TABLE`: content_kind
        # records how the text was *obtained*, chunk_type records how it is
        # *structured*. A schedule on a plotted sheet is a table chunk whose
        # content_kind is a drawing, and both facts are worth keeping.
        if signals.has_table:
            prose += _WEIGHT_TITLE_BLOCK

        return drawing, prose

    def _reason(self, signals: PageSignals, verdict: str, scanned: bool) -> str:
        layer = (
            "text layer unreadable"
            if signals.native_text_words is None
            else f"{signals.native_text_words} native words"
        )
        return (
            f"{verdict}: {signals.words} words, "
            f"{signals.sentence_density:.2f} periods/100ch, "
            f"{signals.title_block_markers} title-block markers, "
            f"{signals.dimension_markers} dimensions, {layer}" + (" (scanned)" if scanned else "")
        )

    # -- per-block -----------------------------------------------------

    def _classify_blocks(self, raw_document: RawDocument) -> frozenset[int]:
        """Which text blocks read as drawing content.

        Block-level rather than page-level so a sheet with a detail drawing
        above and erection notes below is not forced into one type. A block
        is small, so the test is deliberately simple: no terminated
        sentences, or a title-block/dimension callout.
        """
        drawing: set[int] = set()
        for index, block in enumerate(raw_document.text_blocks):
            text = block.text
            if not text.strip():
                continue
            density = len(_SENTENCE_END.findall(text)) / max(len(text), 1) * 100
            if (
                _TITLE_BLOCK.search(text)
                or len(_DIMENSION_MARKER.findall(text)) >= 2
                or density < self._max_sentence_density
            ):
                drawing.add(index)
        return frozenset(drawing)

    def _split_words(
        self, raw_document: RawDocument, page_number: int, drawing_blocks: frozenset[int]
    ) -> tuple[int, int]:
        """Words on a page that sit in drawing-like vs prose-like blocks."""
        drawing_words = 0
        prose_words = 0
        for index, block in enumerate(raw_document.text_blocks):
            if block.page_number != page_number:
                continue
            count = len(block.text.split())
            if index in drawing_blocks:
                drawing_words += count
            else:
                prose_words += count
        # A parsed table is structured prose, not geometry -- see `_score`.
        for table in raw_document.tables:
            if table.page_number == page_number:
                prose_words += len(table.markdown.split())
        return drawing_words, prose_words

    def block_kind(
        self, classification: ContentClassification, block_index: int, page_number: int | None
    ) -> ContentKind:
        """The kind for one block, keeping its page's scanned/native axis.

        A block is drawing-or-prose; whether its text was scanned is a
        property of the page it was printed on.
        """
        page_kind = classification.kind_for_page(page_number)
        is_drawing = block_index in classification.drawing_blocks
        scanned = page_kind.is_scanned or page_kind is ContentKind.SCANNED_DRAWING
        if is_drawing:
            return ContentKind.SCANNED_DRAWING if scanned else ContentKind.VECTOR_DRAWING
        return ContentKind.SCANNED_PROSE if scanned else ContentKind.PROSE

    def refine_kind_for_text(self, text: str, page_kind: ContentKind) -> ContentKind:
        """Narrow a MIXED page's verdict to what one piece of its text is.

        Only meaningful for MIXED: on a uniform page the page's own kind is
        already the right answer, and re-deciding from a short excerpt would
        be less reliable than the page-level evidence, not more.
        """
        if page_kind is not ContentKind.MIXED or not text.strip():
            return page_kind
        density = len(_SENTENCE_END.findall(text)) / max(len(text), 1) * 100
        looks_drawing = (
            _TITLE_BLOCK.search(text) is not None
            or len(_DIMENSION_MARKER.findall(text)) >= 2
            or density < self._max_sentence_density
        )
        return ContentKind.VECTOR_DRAWING if looks_drawing else ContentKind.PROSE

    # -- assembly ------------------------------------------------------

    def _summarize(
        self, pages: dict[int, PageClassification], drawing_blocks: frozenset[int]
    ) -> ContentClassification:
        if not pages:
            return ContentClassification(
                kind=ContentKind.PROSE,
                reason="no pages to classify",
                drawing_blocks=drawing_blocks,
                confidence=0.0,
            )

        kinds = {p.kind for p in pages.values()}
        confidence = sum(p.confidence for p in pages.values()) / len(pages)
        if len(kinds) == 1:
            only = kinds.pop()
            return ContentClassification(
                kind=only,
                reason=f"all {len(pages)} page(s) classified {only.value}",
                pages=pages,
                drawing_blocks=drawing_blocks,
                confidence=confidence,
            )

        counts: dict[str, int] = {}
        for page in pages.values():
            counts[page.kind.value] = counts.get(page.kind.value, 0) + 1
        breakdown = ", ".join(f"{n}x {k}" for k, n in sorted(counts.items()))
        return ContentClassification(
            kind=ContentKind.MIXED,
            reason=f"pages differ ({breakdown}) -- route per page, not per document",
            pages=pages,
            drawing_blocks=drawing_blocks,
            confidence=confidence,
        )

    def _keep_scanned_axis(self, vision_kind: ContentKind, page: PageClassification) -> ContentKind:
        """A model may judge drawing-vs-prose; it may not overrule the file.

        Whether a page has a native text layer is a fact read from the PDF,
        not an opinion, so a vision verdict is mapped onto the axis the
        probe already established.
        """
        if page.signals.has_native_text_layer is not False:
            return vision_kind
        if vision_kind is ContentKind.VECTOR_DRAWING:
            return ContentKind.SCANNED_DRAWING
        if vision_kind is ContentKind.PROSE:
            return ContentKind.SCANNED_PROSE
        return vision_kind

    def _page_numbers(
        self, raw_document: RawDocument, native_by_page: dict[int, int] | None
    ) -> list[int]:
        numbers = {b.page_number for b in raw_document.text_blocks if b.page_number}
        numbers |= {t.page_number for t in raw_document.tables if t.page_number}
        if native_by_page:
            numbers |= set(native_by_page)
        if raw_document.page_count:
            numbers |= set(range(1, raw_document.page_count + 1))
        return sorted(numbers) or [1]

    def _page_text(self, raw_document: RawDocument, page_number: int) -> str:
        """A page's text, tables included.

        Docling resolves a schedule into a `TableBlock`, not text blocks, so
        a page that is mostly table looks nearly empty if only text blocks
        are counted -- and a nearly empty page with a text layer looks like a
        drawing. This is why the table page in the fixture needs it.
        """
        parts = [b.text for b in raw_document.text_blocks if b.page_number == page_number]
        parts += [t.markdown for t in raw_document.tables if t.page_number == page_number]
        return "\n".join(p for p in parts if p)

    # -- file probes ---------------------------------------------------

    def _native_words_by_page(self, file_path: Path) -> dict[int, int] | None:
        """Words in each page's own text layer, or None if unreadable.

        Read with pypdf rather than through the loader on purpose: once
        Docling has finished, an OCR'd scan is indistinguishable from a
        plotted sheet. This is the only point at which the difference is
        still visible -- and it has to be per page, because one scanned page
        among natives disappears completely into a document-wide sum.

        `0` and `None` are different answers: "I read the page and there is
        no text" versus "I could not read the file". Only the first is
        evidence of a scan.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            return {
                number: len((page.extract_text() or "").split())
                for number, page in enumerate(reader.pages, start=1)
            }
        except Exception as exc:
            logger.warning("native_text_probe_failed", file=file_path.name, error=str(exc))
            return None

    def _images_by_page(self, file_path: Path) -> dict[int, tuple[int, float]] | None:
        """Image count and rough page coverage, per page.

        Separates a scanned page from a blank one: both have no text, but
        only one has a full-page raster on it. Coverage is approximate --
        it compares the largest image's pixel area against the page area at
        72 dpi without parsing the content stream for the actual placement
        matrix, which would cost far more than the signal is worth.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(file_path))
            result: dict[int, tuple[int, float]] = {}
            for number, page in enumerate(reader.pages, start=1):
                result[number] = self._page_image_info(page)
            return result
        except Exception as exc:
            logger.warning("image_probe_failed", file=file_path.name, error=str(exc))
            return None

    @staticmethod
    def _page_image_info(page: object) -> tuple[int, float]:
        try:
            resources = page.get("/Resources") or {}  # type: ignore[attr-defined]
            xobjects = resources.get("/XObject") or {}
            if hasattr(xobjects, "get_object"):
                xobjects = xobjects.get_object()

            box = page.mediabox  # type: ignore[attr-defined]
            page_area = float(abs(box.width) * abs(box.height)) or 1.0

            count = 0
            largest = 0.0
            for name in xobjects:
                obj = xobjects[name]
                obj = obj.get_object() if hasattr(obj, "get_object") else obj
                if obj.get("/Subtype") != "/Image":
                    continue
                count += 1
                # Pixel dimensions at 72 dpi, which is the unit the media box
                # is already in.
                area = float(obj.get("/Width", 0)) * float(obj.get("/Height", 0))
                largest = max(largest, area)
            return count, min(largest / page_area, 1.0) if count else 0.0
        except Exception:
            return 0, 0.0


# Formats that are never a scan: there is no raster to recognise, so the
# native-text question does not arise.
_NEVER_SCANNED_EXTENSIONS = frozenset({"docx", "doc", "md", "html", "htm", "txt", "rtf", "odt"})
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp"})


def summarize_kinds(kinds: Sequence[ContentKind]) -> ContentKind:
    """The single kind that describes a set, or MIXED when they disagree."""
    unique = set(kinds)
    if not unique:
        return ContentKind.PROSE
    if len(unique) == 1:
        return unique.pop()
    return ContentKind.MIXED
