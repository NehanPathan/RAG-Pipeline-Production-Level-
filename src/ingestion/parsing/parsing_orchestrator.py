from __future__ import annotations

import statistics
from itertools import pairwise
from pathlib import Path

from src.ingestion.layout.base import LayoutAnalyzer
from src.ingestion.loaders.base import BoundingBox, RawDocument, TextBlock
from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.ocr.models import OCRMetadata, OCRPageResult, OCRResult, OCRWord
from src.ingestion.parsing.drawing_detector import ContentClassification, DrawingContentDetector
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Labels LabeledLayoutAnalyzer actually branches on. Membership in this set
# -- rather than "has any label at all" -- is what decides whether the
# labeled analyzer has anything to work with. OCR-derived blocks now carry a
# label of their own (below), and a bare truthiness test would have silently
# routed every scanned document to the labeled analyzer, which would then
# find no headings, captions or list items and return an empty layout.
_STRUCTURAL_LABELS = frozenset(
    {"section_header", "title", "list_item", "caption", "footnote", "field_key", "field_value"}
)

_OCR_BLOCK_LABEL = "ocr_block"

# A word joins the current line when its vertical centre sits within this
# fraction of the median word height of the line's centre.
_LINE_TOLERANCE = 0.6
# A line starts a new block when the vertical gap above it exceeds this
# multiple of the median word height.
_BLOCK_GAP_RATIO = 1.2


def _y_centre(word: OCRWord) -> float:
    assert word.bbox is not None
    return (word.bbox.y0 + word.bbox.y1) / 2


def _median_height(words: list[OCRWord]) -> float:
    heights = [w.bbox.y1 - w.bbox.y0 for w in words if w.bbox is not None]
    positive = [h for h in heights if h > 0]
    return statistics.median(positive) if positive else 0.0


def group_words_into_lines(words: list[OCRWord], line_height: float) -> list[list[OCRWord]]:
    """Group OCR words into reading-order lines by vertical proximity."""
    ordered = sorted(words, key=lambda w: (_y_centre(w), w.bbox.x0 if w.bbox else 0.0))
    tolerance = line_height * _LINE_TOLERANCE
    lines: list[list[OCRWord]] = []
    for word in ordered:
        if lines and abs(_y_centre(word) - _y_centre(lines[-1][0])) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.bbox.x0 if w.bbox else 0.0) for line in lines]


def group_lines_into_blocks(
    lines: list[list[OCRWord]], line_height: float
) -> list[list[list[OCRWord]]]:
    """Group lines into blocks, breaking wherever the vertical gap widens."""
    if not lines:
        return []
    gap_threshold = line_height * _BLOCK_GAP_RATIO
    blocks: list[list[list[OCRWord]]] = [[lines[0]]]
    for previous, current in pairwise(lines):
        previous_bottom = max(w.bbox.y1 for w in previous if w.bbox is not None)
        current_top = min(w.bbox.y0 for w in current if w.bbox is not None)
        if (current_top - previous_bottom) > gap_threshold:
            blocks.append([current])
        else:
            blocks[-1].append(current)
    return blocks


class DocumentParsingService:
    """Sits between Load and Enrich in IngestionPipeline: runs OCRDetector to
    decide skip-vs-OCR, calls OCRProvider only when required, then picks a
    LayoutAnalyzer and wraps everything into one ParsedDocument.

    Loaders, OCR providers, and layout analyzers each stay single-purpose and
    independently swappable -- this is the one place that knows how to
    combine them (Gap 7, docs/architecture/12_phase4a_design_review.md).
    """

    def __init__(
        self,
        ocr_detector: OCRDetector,
        ocr_provider: OCRProvider,
        labeled_analyzer: LayoutAnalyzer,
        heuristic_analyzer: LayoutAnalyzer,
        content_detector: DrawingContentDetector | None = None,
    ) -> None:
        self._ocr_detector = ocr_detector
        self._ocr_provider = ocr_provider
        self._labeled_analyzer = labeled_analyzer
        self._heuristic_analyzer = heuristic_analyzer
        self._content_detector = content_detector or DrawingContentDetector()

    async def process(
        self,
        raw_document: RawDocument,
        file_path: Path,
        file_type: str,
        language: str = "en",
    ) -> ParsedDocument:
        # Classify before deciding on OCR, not after: whether the file has a
        # text layer of its own is one of the inputs to that decision, and it
        # stops being observable the moment OCR text is merged in.
        classification = self._content_detector.classify(raw_document, file_path, file_type)
        decision = self._ocr_detector.detect(
            raw_document, file_type, has_native_text_layer=classification.has_native_text_layer
        )

        if decision.required:
            logger.info(
                "ocr_required",
                file=str(file_path),
                reason=decision.reason,
                pages=decision.pages_required or "all",
            )
            try:
                ocr_result = await self._ocr_provider.recognize(
                    file_path, language=language, pages=decision.pages_required or None
                )
            except Exception as exc:
                # OCR needs system binaries (tesseract, poppler) that are
                # present in our image but not guaranteed on every host. When
                # the loader already extracted usable text -- which is the
                # case for every scanned PDF Docling OCR'd internally -- that
                # text is worth keeping: losing the whole document is worse
                # than losing the confidence scores and word geometry that
                # our own OCR pass would have added.
                #
                # This branch only became reachable for scanned PDFs when
                # they started being routed to OCR at all. Failing them hard
                # would have made this fix a regression on any deployment
                # without poppler.
                if not raw_document.full_text.strip():
                    raise
                logger.warning(
                    "ocr_failed_keeping_extracted_text",
                    file=str(file_path),
                    error=str(exc),
                    words_kept=raw_document.word_count,
                )
                ocr_metadata = OCRMetadata.skipped(reason=f"OCR failed: {exc}")
                return self._finish(raw_document, ocr_metadata, classification)

            raw_document = self._merge_ocr_result(raw_document, ocr_result)
            ocr_metadata = OCRMetadata.from_result(ocr_result)
        else:
            logger.info("ocr_skipped", file=str(file_path), reason=decision.reason)
            ocr_metadata = OCRMetadata.skipped(reason=decision.reason)

        return self._finish(raw_document, ocr_metadata, classification)

    def _finish(
        self,
        raw_document: RawDocument,
        ocr_metadata: OCRMetadata,
        classification: ContentClassification,
    ) -> ParsedDocument:
        analyzer = self._select_layout_analyzer(raw_document)
        layout = analyzer.analyze(raw_document)
        return ParsedDocument(
            raw=raw_document,
            ocr_metadata=ocr_metadata,
            layout=layout,
            classification=classification,
        )

    def _merge_ocr_result(self, raw_document: RawDocument, ocr_result: OCRResult) -> RawDocument:
        """Replaces the loader's text_blocks *only for the pages OCR actually
        covered* -- other pages' blocks (and their structural labels from
        Docling/Unstructured, Gap 1) are kept untouched.

        OCRDetector only ever asks for OCR on pages it already judged the
        loader's own extraction insufficient for, so within those specific
        pages the OCR text is the source of truth (keeping both would
        double-count near-duplicate/garbage text in later chunking) -- but a
        mixed document (most pages text-rich, one page scanned) must not
        lose the good pages' structure just because one page needed OCR.
        Per-word OCR geometry and confidence *are* threaded onto the
        generated TextBlocks (see `_blocks_from_ocr_page`). Collapsing each
        page into one bbox-less block previously threw away the only
        coordinate data a scanned document ever has -- which is what
        highlighting a matched region needs -- and forced chunk validation to
        judge every chunk by the whole document's mean confidence.
        """
        ocr_pages = {page.page_number for page in ocr_result.pages}
        kept_blocks = [b for b in raw_document.text_blocks if b.page_number not in ocr_pages]
        ocr_blocks = [
            block for page in ocr_result.pages for block in self._blocks_from_ocr_page(page)
        ]
        raw_document.text_blocks = sorted(
            kept_blocks + ocr_blocks, key=lambda b: (b.page_number is None, b.page_number or 0)
        )
        raw_document.word_count = len(raw_document.full_text.split())
        if not raw_document.page_count:
            raw_document.page_count = len(ocr_result.pages)
        return raw_document

    def _blocks_from_ocr_page(self, page: OCRPageResult) -> list[TextBlock]:
        """Turn one OCR'd page into blocks that keep their geometry.

        Words are grouped into lines by vertical proximity and lines into
        blocks by vertical gap, so each block carries a real bounding box
        (the union of its words') and its own mean confidence. Providers that
        return no per-word geometry fall back to the previous whole-page
        block, so nothing regresses for them.
        """
        placed = [w for w in page.words if w.bbox is not None and w.text.strip()]
        line_height = _median_height(placed)
        if not placed or line_height <= 0:
            if not page.text.strip():
                return []
            return [
                TextBlock(
                    text=page.text,
                    page_number=page.page_number,
                    element_label=_OCR_BLOCK_LABEL,
                    ocr_confidence=page.confidence,
                )
            ]

        lines = group_words_into_lines(placed, line_height)
        blocks: list[TextBlock] = []
        for block_lines in group_lines_into_blocks(lines, line_height):
            block_words = [word for line in block_lines for word in line]
            text = "\n".join(" ".join(w.text for w in line) for line in block_lines).strip()
            if not text:
                continue
            boxes = [w.bbox for w in block_words if w.bbox is not None]
            confidences = [w.confidence for w in block_words]
            blocks.append(
                TextBlock(
                    text=text,
                    page_number=page.page_number,
                    element_label=_OCR_BLOCK_LABEL,
                    bbox=BoundingBox(
                        x0=min(b.x0 for b in boxes),
                        y0=min(b.y0 for b in boxes),
                        x1=max(b.x1 for b in boxes),
                        y1=max(b.y1 for b in boxes),
                    ),
                    ocr_confidence=(
                        sum(confidences) / len(confidences) if confidences else page.confidence
                    ),
                )
            )
        return blocks

    def _select_layout_analyzer(self, raw_document: RawDocument) -> LayoutAnalyzer:
        """LabeledLayoutAnalyzer needs at least one structurally-labeled text
        block (from DoclingLoader/UnstructuredLoader, Gap 1) to do anything
        useful; falls back to heuristics for OCR-sourced or plain-text
        documents where no loader ever attached labels.

        The test is membership in `_STRUCTURAL_LABELS`, not mere presence of
        a label: OCR blocks now carry `ocr_block`, and a truthiness test
        would route every scanned document to an analyzer that has nothing
        to read.
        """
        if any(block.element_label in _STRUCTURAL_LABELS for block in raw_document.text_blocks):
            return self._labeled_analyzer
        return self._heuristic_analyzer
