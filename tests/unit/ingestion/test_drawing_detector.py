"""The three PDF cases, and what separates them from a DXF.

The fixtures live in conftest.py and are built rather than committed, so
each one visibly is the case it claims to be -- the vector sheet carries its
text as text, the scanned one has no text layer because it was rasterised.
"""

from __future__ import annotations

import pytest

from src.ingestion.loaders.base import RawDocument, TextBlock
from src.ingestion.parsing.drawing_detector import ContentKind, DrawingContentDetector
from tests.unit.ingestion.conftest import DRAWING_LINES, PROSE_TEXT


def _raw(text_lines: list[str], pages: int = 1) -> RawDocument:
    """A RawDocument as a loader would hand it over.

    The scanned case passes the *same* lines as the vector case on purpose:
    that is exactly what Docling produces, because it OCRs the page
    internally and returns the text as if it had been extracted. If the
    detector could tell them apart from this alone the fixture would be
    lying.
    """
    return RawDocument(
        file_path="sheet.pdf",
        file_name="sheet.pdf",
        mime_type="application/pdf",
        text_blocks=[TextBlock(text=line, page_number=1) for line in text_lines],
        tables=[],
        loader_name="docling",
        page_count=pages,
        word_count=sum(len(line.split()) for line in text_lines),
    )


class TestPdfCases:
    def test_vector_drawing_pdf_is_a_drawing_with_a_text_layer(self, drawing_pdfs):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), drawing_pdfs["vector"], "pdf"
        )

        assert result.kind is ContentKind.VECTOR_DRAWING
        assert result.has_native_text_layer is True
        assert result.kind.is_drawing
        assert not result.kind.is_scanned
        # A plotted dimension is a rendered string, not the CAD value.
        assert not result.kind.has_exact_dimensions

    def test_scanned_drawing_pdf_is_detected_despite_identical_extracted_text(self, drawing_pdfs):
        """The case the pipeline could not previously see.

        Same loader output as the vector sheet -- same words, same blocks,
        same counts. Only the absent text layer distinguishes them.
        """
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), drawing_pdfs["scanned"], "pdf"
        )

        assert result.kind is ContentKind.SCANNED_DRAWING
        assert result.has_native_text_layer is False
        assert result.native_text_words < 10
        assert result.kind.is_drawing
        assert result.kind.is_scanned
        assert not result.kind.has_exact_dimensions

    def test_prose_specification_is_not_reclassified_as_a_drawing(self, drawing_pdfs):
        # Three paragraphs, ~219 words -- the density of a real
        # specification page, and the control for the whole feature.
        result = DrawingContentDetector().classify(
            _raw([PROSE_TEXT] * 3), drawing_pdfs["prose"], "pdf"
        )

        assert result.kind is ContentKind.PROSE
        assert result.has_native_text_layer is True
        assert not result.kind.is_drawing
        # Both signals must point away from a drawing, not just one.
        assert result.sentence_density > 1.0
        assert result.words_per_page > 150

    def test_the_two_drawings_are_distinguishable_only_by_their_text_layer(self, drawing_pdfs):
        """Guards the fixtures themselves.

        If the scanned PDF ever grows a text layer, both drawing tests would
        still pass for the wrong reason.
        """
        detector = DrawingContentDetector()
        vector = detector.classify(_raw(DRAWING_LINES), drawing_pdfs["vector"], "pdf")
        scanned = detector.classify(_raw(DRAWING_LINES), drawing_pdfs["scanned"], "pdf")

        assert vector.words_per_page == scanned.words_per_page
        assert vector.sentence_density == scanned.sentence_density
        assert vector.native_text_words > 10
        assert scanned.native_text_words < 10


class TestCadNativeIsDistinct:
    """A DXF is not a PDF drawing, and the difference has to be visible."""

    @pytest.mark.parametrize("extension", ["dxf", "dwg"])
    def test_cad_files_claim_exact_dimensions(self, extension, tmp_path):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), tmp_path / f"sheet.{extension}", extension
        )

        assert result.kind is ContentKind.CAD_NATIVE
        assert result.kind.is_drawing
        assert result.kind.has_exact_dimensions
        assert "layers" in result.reason

    def test_pdf_drawings_never_claim_exact_dimensions(self, drawing_pdfs):
        detector = DrawingContentDetector()

        for name in ("vector", "scanned"):
            result = detector.classify(_raw(DRAWING_LINES), drawing_pdfs[name], "pdf")
            assert not result.kind.has_exact_dimensions, name

    def test_cad_classification_does_not_read_the_file(self, tmp_path):
        """A DXF is known to be a drawing from its extension alone.

        It never reaches the pypdf probe, so a path that does not exist is
        still classified -- which is what makes the CAD branch free.
        """
        result = DrawingContentDetector().classify(_raw([]), tmp_path / "does-not-exist.dxf", "dxf")

        assert result.kind is ContentKind.CAD_NATIVE


class TestSignals:
    def test_title_block_markers_outweigh_a_wordy_sheet(self, tmp_path):
        """A drawing dense enough to look like prose is still a drawing.

        A general-arrangement sheet covered in notes can exceed the
        words-per-page threshold; its title block is what keeps it correctly
        classified.
        """
        wordy = ["DRAWING NO: S-201", "REV: B", "SHEET NO: 3 OF 8"] + [PROSE_TEXT] * 4

        result = DrawingContentDetector().classify(_raw(wordy), tmp_path / "missing.pdf", "pdf")

        assert result.title_block_markers >= 2
        assert result.kind.is_drawing

    def test_a_short_prose_page_is_not_a_drawing(self, tmp_path):
        """Low word count alone must not be enough.

        A one-paragraph cover note trips the words-per-page test; its
        sentence density is what saves it. Both signals must agree.
        """
        detector = DrawingContentDetector()
        result = detector.classify(
            _raw(["The steelwork described herein is complete. Refer to S-100."]),
            tmp_path / "missing.pdf",
            "pdf",
        )

        assert result.kind is ContentKind.PROSE
        assert not result.kind.is_drawing

    def test_an_unreadable_file_yields_no_opinion_rather_than_a_scan_verdict(self, tmp_path):
        """ "Could not measure" must not be reported as "no text layer".

        The second claim forces a full OCR pass on every page. Inferring it
        from an unreadable or missing file would turn an operational error
        into a silent doubling of ingestion cost across the corpus, so the
        probe declines to answer and the existing word-density rules stand.
        """
        broken = tmp_path / "truncated.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot actually a pdf")

        result = DrawingContentDetector().classify(_raw(DRAWING_LINES), broken, "pdf")

        assert result.has_native_text_layer is None
        assert not result.kind.is_scanned
        # Content shape is still readable from the extracted text.
        assert result.kind is ContentKind.VECTOR_DRAWING
        assert "unreadable" in result.reason

    def test_a_missing_file_does_not_force_ocr(self, tmp_path):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), tmp_path / "never-written.pdf", "pdf"
        )

        assert result.has_native_text_layer is None

    def test_docx_is_never_a_drawing(self, tmp_path):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), tmp_path / "spec.docx", "docx"
        )

        assert result.kind is ContentKind.PROSE
        assert result.has_native_text_layer is None

    @pytest.mark.parametrize("extension", ["png", "jpg", "tiff"])
    def test_an_uploaded_drawing_image_is_a_scanned_drawing(self, extension, tmp_path):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), tmp_path / f"sheet.{extension}", extension
        )

        assert result.kind is ContentKind.SCANNED_DRAWING
        assert result.has_native_text_layer is False
