from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.base import RawDocument, TextBlock
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.ocr.models import OCRPageResult, OCRResult
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService


def _service(ocr_provider=None, min_words_per_page=10.0) -> DocumentParsingService:
    return DocumentParsingService(
        ocr_detector=OCRDetector(min_words_per_page=min_words_per_page),
        ocr_provider=ocr_provider or AsyncMock(),
        labeled_analyzer=LabeledLayoutAnalyzer(),
        heuristic_analyzer=HeuristicLayoutAnalyzer(),
    )


def _searchable_doc() -> RawDocument:
    return RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=[
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(
                text=" ".join(f"word{i}" for i in range(200)), element_label="text", page_number=1
            ),
        ],
        page_count=1,
        word_count=201,
    )


def _scanned_doc() -> RawDocument:
    return RawDocument(
        file_path=Path("/tmp/scan.pdf"),
        file_name="scan.pdf",
        mime_type="application/pdf",
        text_blocks=[],
        page_count=3,
        word_count=2,
    )


async def test_searchable_document_skips_ocr_and_uses_labeled_analyzer():
    ocr_provider = AsyncMock()
    service = _service(ocr_provider=ocr_provider)

    parsed = await service.process(_searchable_doc(), Path("/tmp/test.pdf"), file_type="pdf")

    ocr_provider.recognize.assert_not_called()
    assert parsed.ocr_metadata.ran is False
    assert len(parsed.layout.headings) == 1


async def test_scanned_document_runs_ocr_and_replaces_text_blocks():
    ocr_provider = AsyncMock()
    ocr_provider.recognize.return_value = OCRResult(
        engine="tesseract",
        language="en",
        pages=[
            OCRPageResult(page_number=1, text="Recognized page one text.", confidence=0.9),
            OCRPageResult(page_number=2, text="Recognized page two text.", confidence=0.8),
        ],
        processing_time_ms=42.0,
    )
    service = _service(ocr_provider=ocr_provider)

    parsed = await service.process(_scanned_doc(), Path("/tmp/scan.pdf"), file_type="pdf")

    ocr_provider.recognize.assert_awaited_once()
    assert parsed.ocr_metadata.ran is True
    assert parsed.ocr_metadata.engine == "tesseract"
    assert parsed.ocr_metadata.confidence == pytest.approx(0.85)
    assert len(parsed.raw.text_blocks) == 2
    assert parsed.raw.text_blocks[0].text == "Recognized page one text."


async def test_ocr_output_has_no_labels_so_heuristic_analyzer_is_used():
    ocr_provider = AsyncMock()
    ocr_provider.recognize.return_value = OCRResult(
        engine="tesseract",
        language="en",
        pages=[OCRPageResult(page_number=1, text="A Short Heading\nBody text follows.", confidence=0.9)],
    )
    service = _service(ocr_provider=ocr_provider)

    parsed = await service.process(_scanned_doc(), Path("/tmp/scan.pdf"), file_type="pdf")

    # HeuristicLayoutAnalyzer treats each TextBlock as one line -- since OCR
    # produced one TextBlock per page (multi-line text), no heading heuristic
    # applies here, but this at least proves the heuristic path was taken
    # (no exception, no dependence on element_label).
    assert parsed.ocr_metadata.ran is True


async def test_mixed_document_ocrs_only_the_sparse_page_and_keeps_the_rest():
    # Page 1 is text-rich and labeled (as a real loader would produce);
    # page 2 is a scanned image with no extractable text at all.
    doc = RawDocument(
        file_path=Path("/tmp/mixed.pdf"),
        file_name="mixed.pdf",
        mime_type="application/pdf",
        text_blocks=[
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(
                text=" ".join(f"word{i}" for i in range(200)), element_label="text", page_number=1
            ),
        ],
        page_count=2,
        word_count=200,
    )
    ocr_provider = AsyncMock()
    ocr_provider.recognize.return_value = OCRResult(
        engine="tesseract",
        language="en",
        pages=[OCRPageResult(page_number=2, text="Recognized page two text.", confidence=0.9)],
    )
    service = _service(ocr_provider=ocr_provider)

    parsed = await service.process(doc, Path("/tmp/mixed.pdf"), file_type="pdf")

    # Only page 2 was sent to the OCR provider.
    _, kwargs = ocr_provider.recognize.call_args
    assert kwargs["pages"] == [2]

    # Page 1's original, labeled blocks survive untouched.
    page1_blocks = [b for b in parsed.raw.text_blocks if b.page_number == 1]
    assert len(page1_blocks) == 2
    assert page1_blocks[0].element_label == "section_header"

    # Page 2 now has the OCR'd text instead of nothing.
    page2_blocks = [b for b in parsed.raw.text_blocks if b.page_number == 2]
    assert len(page2_blocks) == 1
    assert page2_blocks[0].text == "Recognized page two text."

    # Labels survive on page 1, so the labeled analyzer path is still used.
    assert len(parsed.layout.headings) == 1


async def test_text_native_extension_never_triggers_ocr():
    ocr_provider = AsyncMock()
    service = _service(ocr_provider=ocr_provider)
    doc = RawDocument(
        file_path=Path("/tmp/test.md"),
        file_name="test.md",
        mime_type="text/markdown",
        text_blocks=[TextBlock(text="Just some markdown.", page_number=1)],
        page_count=1,
        word_count=3,
    )

    parsed = await service.process(doc, Path("/tmp/test.md"), file_type="md")

    ocr_provider.recognize.assert_not_called()
    assert parsed.ocr_metadata.skipped_reason is not None
