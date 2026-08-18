from __future__ import annotations

from pathlib import Path

from src.ingestion.layout.base import LayoutAnalyzer
from src.ingestion.loaders.base import RawDocument, TextBlock
from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.ocr.models import OCRMetadata, OCRResult
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


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
    ) -> None:
        self._ocr_detector = ocr_detector
        self._ocr_provider = ocr_provider
        self._labeled_analyzer = labeled_analyzer
        self._heuristic_analyzer = heuristic_analyzer

    async def process(
        self,
        raw_document: RawDocument,
        file_path: Path,
        file_type: str,
        language: str = "en",
    ) -> ParsedDocument:
        decision = self._ocr_detector.detect(raw_document, file_type)

        if decision.required:
            logger.info(
                "ocr_required",
                file=str(file_path),
                reason=decision.reason,
                pages=decision.pages_required or "all",
            )
            ocr_result = await self._ocr_provider.recognize(
                file_path, language=language, pages=decision.pages_required or None
            )
            raw_document = self._merge_ocr_result(raw_document, ocr_result)
            ocr_metadata = OCRMetadata.from_result(ocr_result)
        else:
            logger.info("ocr_skipped", file=str(file_path), reason=decision.reason)
            ocr_metadata = OCRMetadata.skipped(reason=decision.reason)

        analyzer = self._select_layout_analyzer(raw_document)
        layout = analyzer.analyze(raw_document)

        return ParsedDocument(raw=raw_document, ocr_metadata=ocr_metadata, layout=layout)

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
        Per-word OCR confidence/bbox data stays on `OCRResult`/`ocr_metadata`
        rather than being threaded onto every generated TextBlock; chunk-level
        `ocr_confidence` (Module 7) reads the document-level average instead
        -- a deliberate granularity trade-off, not an oversight.
        """
        ocr_pages = {page.page_number for page in ocr_result.pages}
        kept_blocks = [b for b in raw_document.text_blocks if b.page_number not in ocr_pages]
        ocr_blocks = [
            TextBlock(text=page.text, page_number=page.page_number)
            for page in ocr_result.pages
            if page.text.strip()
        ]
        raw_document.text_blocks = sorted(
            kept_blocks + ocr_blocks, key=lambda b: (b.page_number is None, b.page_number or 0)
        )
        raw_document.word_count = len(raw_document.full_text.split())
        if not raw_document.page_count:
            raw_document.page_count = len(ocr_result.pages)
        return raw_document

    def _select_layout_analyzer(self, raw_document: RawDocument) -> LayoutAnalyzer:
        """LabeledLayoutAnalyzer needs at least one structurally-labeled text
        block (from DoclingLoader/UnstructuredLoader, Gap 1) to do anything
        useful; falls back to heuristics for OCR-sourced or plain-text
        documents where no loader ever attached labels."""
        if any(block.element_label for block in raw_document.text_blocks):
            return self._labeled_analyzer
        return self._heuristic_analyzer
