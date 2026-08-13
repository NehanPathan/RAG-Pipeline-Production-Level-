from pathlib import Path

import pytest

from src.ingestion.loaders.base import RawDocument, TextBlock
from src.ingestion.ocr.detector import OCRDetector


def _raw_doc(words_per_page: dict[int, int], page_count: int) -> RawDocument:
    """Builds a RawDocument with one TextBlock per page, each carrying that
    page's share of words -- realistic per-page distribution (unlike a
    single mega-block), needed to exercise per-page density checks."""
    blocks = [
        TextBlock(text=("word " * words).strip(), page_number=page)
        for page, words in words_per_page.items()
    ]
    total_words = sum(words_per_page.values())
    return RawDocument(
        file_path=Path("/tmp/test"),
        file_name="test",
        mime_type="application/octet-stream",
        text_blocks=blocks,
        page_count=page_count,
        word_count=total_words,
    )


def _uniform_doc(word_count: int, page_count: int) -> RawDocument:
    per_page = word_count // page_count if page_count else word_count
    return _raw_doc({p: per_page for p in range(1, page_count + 1)}, page_count)


@pytest.mark.parametrize("ext", ["docx", "doc", "md", "html", "htm", "txt"])
def test_never_ocr_extensions_are_skipped(ext):
    detector = OCRDetector()
    decision = detector.detect(_uniform_doc(word_count=0, page_count=5), file_type=ext)
    assert decision.required is False


@pytest.mark.parametrize("ext", ["png", "jpg", "jpeg", "tiff", "bmp"])
def test_image_extensions_always_require_ocr(ext):
    detector = OCRDetector()
    # Even a "populated" word count shouldn't matter for images -- there is
    # no loader that extracts text from them, so any text present would be
    # fabricated test data, not a signal to trust.
    decision = detector.detect(_uniform_doc(word_count=500, page_count=1), file_type=ext)
    assert decision.required is True
    assert decision.pages_required == [1]


def test_searchable_pdf_skips_ocr():
    detector = OCRDetector(min_words_per_page=10.0)
    raw_doc = _uniform_doc(word_count=3000, page_count=10)  # 300 words/page
    decision = detector.detect(raw_doc, file_type="pdf")
    assert decision.required is False
    assert "searchable" in decision.reason
    assert decision.pages_required == []


def test_scanned_pdf_requires_ocr():
    detector = OCRDetector(min_words_per_page=10.0)
    raw_doc = _uniform_doc(word_count=5, page_count=10)  # 0.5 words/page, every page sparse
    decision = detector.detect(raw_doc, file_type="pdf")
    assert decision.required is True
    assert "low text density" in decision.reason
    assert decision.pages_required == list(range(1, 11))


def test_mixed_document_only_flags_the_sparse_page():
    detector = OCRDetector(min_words_per_page=10.0)
    # Page 1 and 3 are text-rich; page 2 is a scanned image with no
    # extractable text at all -- the real-world case this fix targets.
    raw_doc = _raw_doc({1: 500, 2: 0, 3: 400}, page_count=3)
    decision = detector.detect(raw_doc, file_type="pdf")
    assert decision.required is True
    assert decision.pages_required == [2]
    assert "1 of 3" in decision.reason


def test_pdf_with_zero_pages_does_not_crash():
    detector = OCRDetector()
    raw_doc = RawDocument(
        file_path=Path("/tmp/test"),
        file_name="test",
        mime_type="application/octet-stream",
        text_blocks=[],
        page_count=0,
        word_count=0,
    )
    decision = detector.detect(raw_doc, file_type="pdf")
    assert decision.required is True


def test_unrecognized_extension_defaults_to_skip():
    detector = OCRDetector()
    decision = detector.detect(_uniform_doc(word_count=0, page_count=1), file_type="xyz")
    assert decision.required is False


# A PDF with no text layer of its own. Word density cannot see this case:
# Docling OCRs a scanned page internally and returns the result as ordinary
# text, so the document arrives looking text-rich. What it lacks is a
# confidence score and word coordinates -- which is why our own OCR has to
# run anyway. See src/ingestion/parsing/drawing_detector.py.


def test_pdf_without_a_text_layer_is_ocred_however_text_rich_it_looks():
    detector = OCRDetector(min_words_per_page=10.0)
    # 300 words/page: comfortably above the density floor, and skipped
    # before this. Every one of those words came from someone else's OCR.
    raw_doc = _uniform_doc(word_count=900, page_count=3)

    decision = detector.detect(raw_doc, file_type="pdf", has_native_text_layer=False)

    assert decision.required is True
    assert decision.pages_required == [1, 2, 3]
    assert "no native text layer" in decision.reason


def test_pdf_with_a_text_layer_still_follows_the_density_rules():
    detector = OCRDetector(min_words_per_page=10.0)

    rich = detector.detect(
        _uniform_doc(word_count=3000, page_count=10), file_type="pdf", has_native_text_layer=True
    )
    sparse = detector.detect(
        _raw_doc({1: 500, 2: 0, 3: 400}, page_count=3), file_type="pdf", has_native_text_layer=True
    )

    assert rich.required is False
    # A page can be blank in an otherwise native PDF -- having a text layer
    # is not a reason to stop checking individual pages.
    assert sparse.required is True
    assert sparse.pages_required == [2]


def test_an_unknown_text_layer_preserves_the_original_behaviour():
    """`None` means "no opinion" and must not be read as "no text layer"."""
    detector = OCRDetector(min_words_per_page=10.0)
    raw_doc = _uniform_doc(word_count=3000, page_count=10)

    assert detector.detect(raw_doc, file_type="pdf").required is False
    assert detector.detect(raw_doc, file_type="pdf", has_native_text_layer=None).required is False


def test_forcing_ocr_on_scanned_pdfs_can_be_switched_off():
    """The escape hatch for a deployment where re-OCRing scans costs too much."""
    detector = OCRDetector(min_words_per_page=10.0, ocr_pdfs_without_text_layer=False)

    decision = detector.detect(
        _uniform_doc(word_count=900, page_count=3), file_type="pdf", has_native_text_layer=False
    )

    assert decision.required is False
