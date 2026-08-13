"""Per-page classification, including one PDF that is five things at once.

Fixtures live in conftest.py and are built rather than committed, so each
one visibly is the case it claims to be -- the vector sheet carries its text
as text, the scanned pages have no text layer because they were rasterised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock
from src.ingestion.parsing.drawing_detector import (
    AMBIGUOUS_CONFIDENCE,
    ContentKind,
    DrawingContentDetector,
)
from tests.unit.ingestion.conftest import DRAWING_LINES, MIXED_PAGE_KINDS, PROSE_TEXT


def _raw(
    text_lines: list[str], pages: int = 1, tables: list[TableBlock] | None = None
) -> RawDocument:
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
        tables=tables or [],
        loader_name="docling",
        page_count=pages,
        word_count=sum(len(line.split()) for line in text_lines),
    )


class TestMixedDocument:
    """One PDF, five pages, five kinds of content.

    This is the case a document-level classifier cannot express. Before
    per-page classification the whole document came back `vector_drawing` at
    111 words/page and 1.34 periods/100 chars -- mid-range on both signals,
    wrong for four pages, and it summed the text layer to 458 words so the
    scanned page was invisible and never reached OCR.
    """

    def test_every_page_keeps_its_own_kind(self, mixed_classification):
        actual = {n: p.kind.value for n, p in sorted(mixed_classification.pages.items())}

        assert actual == MIXED_PAGE_KINDS

    def test_the_document_summary_admits_the_pages_disagree(self, mixed_classification):
        assert mixed_classification.kind is ContentKind.MIXED
        assert not mixed_classification.is_uniform
        assert "route per page" in mixed_classification.reason

    def test_only_the_scanned_page_lacks_a_text_layer(self, mixed_classification):
        """The signal that vanishes when it is summed across a document."""
        assert mixed_classification.pages_without_text_layer == [4]
        # No single honest document-wide answer exists, so it declines to
        # give one rather than reporting the majority.
        assert mixed_classification.has_native_text_layer is None

    def test_the_drawing_pages_are_recognised_as_drawings(self, mixed_classification):
        for page in (3, 4, 5):
            assert mixed_classification.kind_for_page(page).is_drawing, page

    def test_the_prose_and_table_pages_are_not(self, mixed_classification):
        for page in (1, 2):
            assert not mixed_classification.kind_for_page(page).is_drawing, page

    def test_the_scanned_page_is_the_only_scanned_one(self, mixed_classification):
        scanned = {n for n, p in mixed_classification.pages.items() if p.kind.is_scanned}

        assert scanned == {4}

    def test_no_page_claims_exact_dimensions(self, mixed_classification):
        """Only a DXF can. Every page here is a picture of geometry."""
        assert not any(p.kind.has_exact_dimensions for p in mixed_classification.pages.values())

    def test_confident_about_the_unambiguous_pages(self, mixed_classification):
        for page in (1, 3, 4):
            assert mixed_classification.pages[page].confidence >= 0.9, page

    def test_confident_that_the_mixed_page_is_mixed(self, mixed_classification):
        """A mixed page's drawing-vs-prose margin is near zero by definition.

        Reporting that as the confidence would send every correctly
        identified mixed page to the ambiguity fallback. What is scored is
        certainty that both are present.
        """
        page = mixed_classification.pages[5]

        assert page.kind is ContentKind.MIXED
        assert page.confidence >= AMBIGUOUS_CONFIDENCE
        assert page.signals.drawing_words >= 5
        assert page.signals.prose_words >= 40

    def test_an_unknown_page_falls_back_to_the_document_summary(self, mixed_classification):
        assert mixed_classification.kind_for_page(99) is ContentKind.MIXED
        assert mixed_classification.kind_for_page(None) is ContentKind.MIXED


class TestSinglePurposeDocuments:
    def test_vector_drawing_pdf(self, drawing_pdfs):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), drawing_pdfs["vector"], "pdf"
        )

        assert result.kind is ContentKind.VECTOR_DRAWING
        assert result.is_uniform
        assert result.has_native_text_layer is True
        assert not result.kind.has_exact_dimensions

    def test_scanned_drawing_pdf_is_detected_despite_identical_extracted_text(self, drawing_pdfs):
        """Same loader output as the vector sheet -- same words, same blocks.
        Only the absent text layer distinguishes them."""
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), drawing_pdfs["scanned"], "pdf"
        )

        assert result.kind is ContentKind.SCANNED_DRAWING
        assert result.pages_without_text_layer == [1]
        assert result.pages[1].signals.native_text_words == 0

    def test_prose_specification_is_not_reclassified(self, drawing_pdfs):
        result = DrawingContentDetector().classify(
            _raw([PROSE_TEXT] * 3), drawing_pdfs["prose"], "pdf"
        )

        assert result.kind is ContentKind.PROSE
        assert result.pages[1].confidence >= 0.9

    def test_the_two_drawings_differ_only_in_their_text_layer(self, drawing_pdfs):
        """Guards the fixtures themselves: if the scanned PDF ever grew a
        text layer, both drawing tests would pass for the wrong reason."""
        detector = DrawingContentDetector()
        vector = detector.classify(_raw(DRAWING_LINES), drawing_pdfs["vector"], "pdf").pages[1]
        scanned = detector.classify(_raw(DRAWING_LINES), drawing_pdfs["scanned"], "pdf").pages[1]

        assert vector.signals.sentence_density == scanned.signals.sentence_density
        assert vector.signals.native_text_words > 10
        assert scanned.signals.native_text_words == 0


class TestNoModelIsCalled:
    """Classification is deterministic and offline. It must stay that way.

    A per-page model call would cost more than the ingestion it serves and
    would make the result irreproducible between runs.
    """

    def test_classify_makes_no_network_call(self, drawing_pdfs, mixed_raw_document, monkeypatch):
        import socket

        def refuse(*args, **kwargs):
            raise AssertionError("classification must not open a socket")

        monkeypatch.setattr(socket.socket, "connect", refuse)

        result = DrawingContentDetector().classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")

        assert result.pages

    def test_the_same_input_always_gives_the_same_answer(self, drawing_pdfs, mixed_raw_document):
        detector = DrawingContentDetector()

        first = detector.classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")
        second = detector.classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")

        assert {n: p.kind for n, p in first.pages.items()} == {
            n: p.kind for n, p in second.pages.items()
        }


class _Recorder:
    """A stand-in vision classifier that records which pages it was asked about."""

    def __init__(self, kind: ContentKind = ContentKind.VECTOR_DRAWING) -> None:
        self.calls: list[int] = []
        self._kind = kind

    async def classify_page(self, file_path: Path, page_number: int) -> ContentKind | None:
        self.calls.append(page_number)
        return self._kind


class TestVisionFallback:
    """Optional, off by default, and only for genuinely ambiguous pages."""

    async def test_no_classifier_configured_means_no_fallback(
        self, drawing_pdfs, mixed_raw_document
    ):
        detector = DrawingContentDetector()

        result = await detector.classify_with_fallback(
            mixed_raw_document, drawing_pdfs["mixed"], "pdf"
        )

        assert {n: p.kind.value for n, p in result.pages.items()} == MIXED_PAGE_KINDS

    async def test_confident_pages_are_never_sent_to_the_model(
        self, drawing_pdfs, mixed_raw_document
    ):
        recorder = _Recorder()
        detector = DrawingContentDetector(vision_classifier=recorder)

        # Measured before the fallback runs: it overwrites the confidence of
        # any page it resolves, so reading it afterwards would not show which
        # pages were confident to begin with.
        deterministic = detector.classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")
        confident = {
            n for n, p in deterministic.pages.items() if p.confidence >= AMBIGUOUS_CONFIDENCE
        }
        await detector.classify_with_fallback(mixed_raw_document, drawing_pdfs["mixed"], "pdf")

        assert len(confident) >= 4, "the fixture should resolve most pages without a model"
        assert not set(recorder.calls) & confident

    async def test_only_ambiguous_pages_are_sent(self, drawing_pdfs, mixed_raw_document):
        recorder = _Recorder()
        detector = DrawingContentDetector(vision_classifier=recorder)

        deterministic = detector.classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")
        ambiguous = {
            n for n, p in deterministic.pages.items() if p.confidence < AMBIGUOUS_CONFIDENCE
        }
        await detector.classify_with_fallback(mixed_raw_document, drawing_pdfs["mixed"], "pdf")

        assert set(recorder.calls) == ambiguous

    async def test_a_model_may_not_overrule_the_file_about_a_text_layer(
        self, drawing_pdfs, mixed_raw_document
    ):
        """Whether a page has a text layer is read from the PDF, not judged.

        A vision verdict of "drawing" on a page known to be scanned means
        scanned drawing, never vector.
        """
        detector = DrawingContentDetector(
            vision_classifier=_Recorder(ContentKind.VECTOR_DRAWING),
            ambiguous_confidence=1.1,  # force every page through the fallback
        )

        result = await detector.classify_with_fallback(
            mixed_raw_document, drawing_pdfs["mixed"], "pdf"
        )

        assert result.pages[4].kind is ContentKind.SCANNED_DRAWING
        assert result.pages[3].kind is ContentKind.VECTOR_DRAWING

    async def test_a_failing_fallback_keeps_the_deterministic_verdict(
        self, drawing_pdfs, mixed_raw_document
    ):
        class Broken:
            async def classify_page(self, file_path, page_number):
                raise RuntimeError("vision endpoint down")

        detector = DrawingContentDetector(vision_classifier=Broken(), ambiguous_confidence=1.1)

        result = await detector.classify_with_fallback(
            mixed_raw_document, drawing_pdfs["mixed"], "pdf"
        )

        assert {n: p.kind.value for n, p in result.pages.items()} == MIXED_PAGE_KINDS


class TestCadNativeIsDistinct:
    """A DXF is not a PDF drawing, and the difference has to be visible."""

    @pytest.mark.parametrize("extension", ["dxf", "dwg"])
    def test_cad_files_claim_exact_dimensions(self, extension, tmp_path):
        result = DrawingContentDetector().classify(
            _raw(DRAWING_LINES), tmp_path / f"sheet.{extension}", extension
        )

        assert result.kind is ContentKind.CAD_NATIVE
        assert result.kind.has_exact_dimensions
        assert "layers" in result.reason

    def test_pdf_drawings_never_claim_exact_dimensions(self, drawing_pdfs):
        detector = DrawingContentDetector()

        for name in ("vector", "scanned", "mixed"):
            result = detector.classify(_raw(DRAWING_LINES), drawing_pdfs[name], "pdf")
            assert not result.kind.has_exact_dimensions, name

    def test_cad_classification_does_not_read_the_file(self, tmp_path):
        """Known from the extension alone, so the probe is never reached."""
        result = DrawingContentDetector().classify(_raw([]), tmp_path / "gone.dxf", "dxf")

        assert result.kind is ContentKind.CAD_NATIVE


class TestSignals:
    def test_a_table_page_is_prose_not_a_drawing(self, tmp_path):
        """A schedule has no terminated sentences and few words, so it trips
        both statistical drawing signals at once. The parsed table grid is
        what settles it -- without that weight the reference fixture's table
        page scored an exact tie."""
        table = TableBlock(
            markdown="| Mark | Section |\n|---|---|\n| B-14 | ISMB 300 |",
            page_number=1,
            row_count=2,
            col_count=2,
        )
        raw = _raw(["BEAM SCHEDULE"], tables=[table])

        result = DrawingContentDetector().classify(raw, tmp_path / "missing.pdf", "pdf")

        assert result.kind_for_page(1) is ContentKind.PROSE
        assert result.pages[1].signals.has_table

    def test_title_block_markers_outweigh_a_wordy_sheet(self, tmp_path):
        """A general-arrangement sheet covered in notes can exceed the
        words-per-page threshold; its title block keeps it classified."""
        wordy = ["DRAWING NO: S-201", "REV: B", "SHEET NO: 3 OF 8"] + [PROSE_TEXT] * 4

        result = DrawingContentDetector().classify(_raw(wordy), tmp_path / "missing.pdf", "pdf")

        assert result.pages[1].signals.title_block_markers >= 2
        assert result.kind.is_drawing

    def test_a_short_prose_page_is_not_a_drawing(self, tmp_path):
        """Low word count alone must not be enough -- a one-paragraph cover
        note trips the words-per-page test; sentence density saves it."""
        result = DrawingContentDetector().classify(
            _raw(["The steelwork described herein is complete. Refer to S-100."]),
            tmp_path / "missing.pdf",
            "pdf",
        )

        assert not result.kind.is_drawing

    def test_an_unreadable_file_yields_no_opinion_rather_than_a_scan_verdict(self, tmp_path):
        """ "Could not measure" must not be reported as "no text layer".

        The second claim forces a full OCR pass. Inferring it from an
        unreadable file would turn an operational error into a silent
        doubling of ingestion cost across the corpus.
        """
        broken = tmp_path / "truncated.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot actually a pdf")

        result = DrawingContentDetector().classify(_raw(DRAWING_LINES), broken, "pdf")

        assert result.pages[1].signals.has_native_text_layer is None
        assert result.pages_without_text_layer == []
        assert not result.kind.is_scanned

    def test_a_blank_page_is_not_a_scan(self, tmp_path):
        """No text and no image is an empty page, not a picture of one."""
        result = DrawingContentDetector().classify(_raw([], pages=1), tmp_path / "gone.pdf", "pdf")

        assert not result.kind.is_scanned

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
        assert result.pages_without_text_layer == [1]


class TestBlockLevelRefinement:
    """A mixed page's chunks are labelled by their own text, not the page's."""

    def test_drawing_text_on_a_mixed_page_reads_as_a_drawing(self):
        kind = DrawingContentDetector().refine_kind_for_text(
            "DRAWING NO: S-207\nREV: A   SCALE 1:50\nPLATE 400x400x20", ContentKind.MIXED
        )

        assert kind is ContentKind.VECTOR_DRAWING

    def test_prose_on_a_mixed_page_reads_as_prose(self):
        kind = DrawingContentDetector().refine_kind_for_text(PROSE_TEXT, ContentKind.MIXED)

        assert kind is ContentKind.PROSE

    @pytest.mark.parametrize(
        "page_kind",
        [ContentKind.PROSE, ContentKind.VECTOR_DRAWING, ContentKind.SCANNED_DRAWING],
    )
    def test_a_uniform_page_is_never_second_guessed(self, page_kind):
        """Page-level evidence beats a short excerpt, so refinement only
        applies where the page really holds both."""
        detector = DrawingContentDetector()

        assert detector.refine_kind_for_text(PROSE_TEXT, page_kind) is page_kind
