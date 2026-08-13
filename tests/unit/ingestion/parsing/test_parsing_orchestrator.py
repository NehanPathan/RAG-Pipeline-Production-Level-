from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.base import BoundingBox, RawDocument, TextBlock
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.ocr.models import OCRPageResult, OCRResult, OCRWord
from src.ingestion.parsing.drawing_detector import ContentKind
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService
from tests.unit.ingestion.conftest import DRAWING_LINES, PROSE_TEXT


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


def _word(text: str, x0: float, y0: float, x1: float, y1: float, conf: float) -> OCRWord:
    return OCRWord(text=text, confidence=conf, bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1))


class TestOCRBlockGeometry:
    """Regression: each OCR'd page was collapsed into a single TextBlock with
    no bbox, no label and no per-block confidence -- discarding the only
    coordinate data a scanned document ever has, which is exactly what
    highlighting a matched region needs."""

    @staticmethod
    def _page_with_words() -> OCRPageResult:
        # Two visually separate groups: a title block at the top and a
        # schedule further down the sheet.
        return OCRPageResult(
            page_number=1,
            text="DRG No S-101 REV C\nISMB 300 SPAN 6000",
            confidence=0.7,
            words=[
                _word("DRG", 10, 10, 40, 20, 0.95),
                _word("No", 45, 10, 60, 20, 0.93),
                _word("S-101", 65, 10, 110, 20, 0.91),
                _word("REV", 10, 25, 40, 35, 0.90),
                _word("C", 45, 25, 55, 35, 0.88),
                # Large vertical gap -- a separate block.
                _word("ISMB", 10, 200, 50, 210, 0.60),
                _word("300", 55, 200, 85, 210, 0.58),
                _word("SPAN", 10, 215, 50, 225, 0.55),
                _word("6000", 55, 215, 95, 225, 0.53),
            ],
        )

    async def _parse(self):
        ocr_provider = AsyncMock()
        ocr_provider.recognize.return_value = OCRResult(
            engine="tesseract", language="en", pages=[self._page_with_words()]
        )
        return await _service(ocr_provider=ocr_provider).process(
            _scanned_doc(), Path("/tmp/scan.pdf"), file_type="pdf"
        )

    async def test_words_are_grouped_into_separate_blocks(self):
        parsed = await self._parse()

        assert len(parsed.raw.text_blocks) == 2, (
            "the title block and the schedule are visually separate and must not merge"
        )

    async def test_each_block_keeps_a_bounding_box(self):
        parsed = await self._parse()

        title_block = parsed.raw.text_blocks[0]
        assert title_block.bbox is not None
        assert title_block.bbox.x0 == 10
        assert title_block.bbox.y0 == 10
        assert title_block.bbox.x1 == 110
        assert title_block.bbox.y1 == 35

    async def test_confidence_is_per_block_not_per_document(self):
        parsed = await self._parse()

        crisp, smudged = parsed.raw.text_blocks
        assert crisp.ocr_confidence == pytest.approx(0.914, abs=0.01)
        assert smudged.ocr_confidence == pytest.approx(0.565, abs=0.01)
        assert crisp.ocr_confidence > smudged.ocr_confidence

    async def test_reading_order_within_a_line_is_left_to_right(self):
        parsed = await self._parse()

        assert parsed.raw.text_blocks[0].text.startswith("DRG No S-101")

    async def test_ocr_blocks_do_not_hijack_the_labeled_analyzer(self):
        """OCR blocks carry a label of their own now. A bare truthiness test
        on element_label would route every scanned document to the labeled
        analyzer, which reads only structural labels and would therefore
        return an empty layout for every scan."""
        service = _service()
        parsed = await self._parse()

        assert all(b.element_label == "ocr_block" for b in parsed.raw.text_blocks)
        assert isinstance(
            service._select_layout_analyzer(parsed.raw), HeuristicLayoutAnalyzer
        )

    async def test_a_structurally_labeled_block_still_selects_the_labeled_analyzer(self):
        service = _service()
        doc = _searchable_doc()

        assert isinstance(service._select_layout_analyzer(doc), LabeledLayoutAnalyzer)


async def test_ocr_page_without_word_geometry_falls_back_to_one_block():
    """Providers that return no per-word boxes must keep working unchanged."""
    ocr_provider = AsyncMock()
    ocr_provider.recognize.return_value = OCRResult(
        engine="tesseract",
        language="en",
        pages=[OCRPageResult(page_number=1, text="Whole page text.", confidence=0.77)],
    )
    service = _service(ocr_provider=ocr_provider)

    parsed = await service.process(_scanned_doc(), Path("/tmp/scan.pdf"), file_type="pdf")

    assert len(parsed.raw.text_blocks) == 1
    assert parsed.raw.text_blocks[0].text == "Whole page text."
    assert parsed.raw.text_blocks[0].bbox is None
    assert parsed.raw.text_blocks[0].ocr_confidence == pytest.approx(0.77)


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


# --- Drawing PDFs ----------------------------------------------------------
#
# Exported CAD drawings arrive as PDFs far more often than as DXF, and the
# service is where the two questions about them get answered: is this a
# drawing, and did its text come from the file or from an OCR engine. Both
# fixtures below are real PDFs (see conftest.py) because the second question
# is only answerable by reading the file -- Docling's output is identical
# either way.


async def _parse(pdf_path, blocks, ocr_provider=None):
    service = _service(ocr_provider=ocr_provider or AsyncMock())
    raw = RawDocument(
        file_path=pdf_path,
        file_name=pdf_path.name,
        mime_type="application/pdf",
        text_blocks=[TextBlock(text=line, page_number=1) for line in blocks],
        page_count=1,
        word_count=sum(len(line.split()) for line in blocks),
    )
    return await service.process(raw, pdf_path, file_type="pdf")


async def test_vector_drawing_pdf_is_classified_without_running_ocr(drawing_pdfs):
    """Text plotted from CAD is already text -- OCR would only degrade it."""
    ocr_provider = AsyncMock()

    parsed = await _parse(drawing_pdfs["vector"], DRAWING_LINES, ocr_provider)

    ocr_provider.recognize.assert_not_called()
    assert parsed.content_kind is ContentKind.VECTOR_DRAWING
    assert parsed.classification.has_native_text_layer is True


async def test_scanned_drawing_pdf_is_ocred_despite_looking_text_rich(drawing_pdfs):
    """The gap this closes.

    Docling OCRs the scan internally and returns ~60 words, so the density
    check saw a healthy page and skipped OCR. Those words carried no
    confidence and no coordinates, which left the chunk validator judging a
    drawing by a number it did not have and highlighting with no geometry.
    """
    ocr_provider = AsyncMock()
    ocr_provider.recognize.return_value = OCRResult(
        engine="tesseract",
        language="en",
        pages=[
            OCRPageResult(
                page_number=1,
                text="DRAWING NO: S-104",
                confidence=0.28,
                words=[
                    OCRWord(text="DRAWING", confidence=0.3, bbox=BoundingBox(10, 10, 60, 20)),
                    OCRWord(text="NO:", confidence=0.25, bbox=BoundingBox(62, 10, 80, 20)),
                    OCRWord(text="S-104", confidence=0.29, bbox=BoundingBox(82, 10, 120, 20)),
                ],
            )
        ],
    )

    parsed = await _parse(drawing_pdfs["scanned"], DRAWING_LINES, ocr_provider)

    ocr_provider.recognize.assert_called_once()
    assert parsed.content_kind is ContentKind.SCANNED_DRAWING
    assert parsed.classification.has_native_text_layer is False
    # The two things Docling's internal OCR never surfaced.
    assert parsed.ocr_metadata.confidence == pytest.approx(0.28)
    assert any(block.bbox is not None for block in parsed.raw.text_blocks)


async def test_a_prose_specification_is_unaffected(drawing_pdfs):
    """The control. Nothing about the ordinary path may change."""
    ocr_provider = AsyncMock()

    parsed = await _parse(drawing_pdfs["prose"], [PROSE_TEXT] * 3, ocr_provider)

    ocr_provider.recognize.assert_not_called()
    assert parsed.content_kind is ContentKind.PROSE
    assert parsed.ocr_metadata.ran is False


async def test_a_failed_ocr_keeps_the_text_the_loader_already_extracted(drawing_pdfs):
    """OCR needs system binaries that not every host has.

    Routing scanned PDFs to OCR made this path reachable for documents that
    previously ingested fine, so a missing poppler or tesseract must not turn
    the fix into a regression. The confidence scores and word geometry are
    lost; the document is not.
    """
    ocr_provider = AsyncMock()
    ocr_provider.recognize.side_effect = RuntimeError("Is poppler installed and in PATH?")

    parsed = await _parse(drawing_pdfs["scanned"], DRAWING_LINES, ocr_provider)

    ocr_provider.recognize.assert_called_once()
    assert parsed.ocr_metadata.ran is False
    assert "OCR failed" in (parsed.ocr_metadata.skipped_reason or "")
    assert "DRAWING NO: S-104" in parsed.full_text
    # Still known to be a scanned drawing -- the classification does not
    # depend on OCR having succeeded.
    assert parsed.content_kind is ContentKind.SCANNED_DRAWING


async def test_a_failed_ocr_on_a_document_with_no_text_still_raises(drawing_pdfs):
    """Nothing to fall back to. Silently indexing an empty document is worse
    than reporting the failure."""
    ocr_provider = AsyncMock()
    ocr_provider.recognize.side_effect = RuntimeError("tesseract is not installed")

    with pytest.raises(RuntimeError, match="tesseract"):
        await _parse(drawing_pdfs["scanned"], [], ocr_provider)
