"""Fixtures over the shared PDF builders in `tests/pdf_fixtures.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pdf_fixtures import (
    DRAWING_LINES,
    ERECTION_NOTES,
    MIXED_PAGE_KINDS,
    PROSE_TEXT,
    TABLE_ROWS,
    build_drawing_pdfs,
)

__all__ = [
    "DRAWING_LINES",
    "ERECTION_NOTES",
    "MIXED_PAGE_KINDS",
    "PROSE_TEXT",
    "TABLE_ROWS",
]


@pytest.fixture(scope="session")
def drawing_pdfs(tmp_path_factory) -> dict[str, Path]:
    pytest.importorskip("matplotlib", reason="fixtures need matplotlib")
    return build_drawing_pdfs(tmp_path_factory.mktemp("drawing_pdfs"))


# What Docling actually returns for `mixed_document.pdf`, captured from a
# real run. Reproduced here rather than invoking Docling in the unit suite,
# which is how every other loader test in this package works -- but the PDF
# itself is real, because the native-text-layer probe reads the file
# directly and stubbing that would defeat the entire test.
#
# Page 4's `BQLTS: M20?60` is not a typo. Docling OCR'd that page with its
# internal RapidOCR and made those errors, which is exactly why the pipeline
# cannot tell a scan from a plotted sheet by looking at extracted text.
MIXED_DOCLING_BLOCKS: list[tuple[int, str, str]] = [
    (1, "text", PROSE_TEXT * 3),
    (2, "page_header", "BEAM AND COLUMN SCHEDULE"),
    (3, "text", "6000"),
    (3, "text", "DRAWING NO: S-104"),
    (3, "text", "REV: C"),
    (3, "text", "SCALE 1:100"),
    (3, "text", "ROOF FRAMING PLAN"),
    (3, "text", "B-14  ISMB 300  SPAN 6000"),
    (3, "text", "B-15  ISMB 400  SPAN 7500"),
    (3, "text", "ALL DIMENSIONS IN MM"),
    (3, "text", "BOLTS: M20x60 GRADE 8.8"),
    (4, "text", "DRAWING NO: S-104"),
    (4, "text", "REV: C"),
    (4, "text", "SCALE 1:100"),
    (4, "text", "ROOF FRAMING PLAN"),
    (4, "text", "B-14 ISMB 300 SPAN 6000"),
    (4, "text", "B-15 ISMB 400 SPAN 7500"),
    (4, "text", "ALL DIMENSIONS IN MM"),
    (4, "text", "BQLTS: M20�60 GRADE 8.8"),
    (4, "text", "6000"),
    (4, "text", "4"),
    (5, "text", "DRAWING NO: S-207 REV: A   SCALE 1:50 BASE PLATE DETAIL"),
    (5, "text", "PLATE 400x400x20  Fe 410 4 NOS M24 ANCHOR BOLTS"),
    (5, "section_header", "NOTES ON BASE PLATE ERECTION"),
    (5, "text", ERECTION_NOTES * 2),
]

MIXED_TABLE_MARKDOWN = "\n".join(
    ["| Mark | Section | Grade | Length | Qty | Mass |", "| --- | --- | --- | --- | --- | --- |"]
    + ["| " + " | ".join(row.split()) + " |" for row in TABLE_ROWS[1:]]
)


@pytest.fixture(scope="session")
def mixed_raw_document():
    """The loader's view of `mixed_document.pdf`, as Docling really returns it."""
    from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock

    blocks = [
        TextBlock(text=text, page_number=page, element_label=label)
        for page, label, text in MIXED_DOCLING_BLOCKS
    ]
    return RawDocument(
        file_path="mixed_document.pdf",
        file_name="mixed_document.pdf",
        mime_type="application/pdf",
        text_blocks=blocks,
        tables=[TableBlock(markdown=MIXED_TABLE_MARKDOWN, page_number=2, row_count=7, col_count=6)],
        loader_name="docling",
        page_count=5,
        word_count=sum(len(b.text.split()) for b in blocks),
    )


@pytest.fixture(scope="session")
def mixed_classification(drawing_pdfs, mixed_raw_document):
    """The five-page document classified: real file, real loader output."""
    from src.ingestion.parsing.drawing_detector import DrawingContentDetector

    return DrawingContentDetector().classify(mixed_raw_document, drawing_pdfs["mixed"], "pdf")
