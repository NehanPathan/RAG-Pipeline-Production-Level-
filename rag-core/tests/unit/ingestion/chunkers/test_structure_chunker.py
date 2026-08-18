from pathlib import Path

from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock
from src.ingestion.ocr.models import OCRMetadata
from src.ingestion.parsing.parsed_document import ParsedDocument


def _parsed(blocks: list[TextBlock], tables=None) -> ParsedDocument:
    raw = RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=blocks,
        tables=tables or [],
        page_count=1,
    )
    return ParsedDocument(raw=raw, ocr_metadata=OCRMetadata.skipped("test"), layout=DocumentLayout())


def test_paragraphs_grouped_under_their_heading():
    chunker = StructureChunker()
    doc = _parsed(
        [
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text="First paragraph.", element_label="text", page_number=1),
            TextBlock(text="Second paragraph.", element_label="text", page_number=1),
        ]
    )
    sections = chunker.split(doc)
    assert len(sections) == 1
    assert sections[0].section_title == "Chapter 1"
    assert "First paragraph." in sections[0].text
    assert "Second paragraph." in sections[0].text


def test_new_heading_starts_a_new_section():
    chunker = StructureChunker()
    doc = _parsed(
        [
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text="Body A.", element_label="text", page_number=1),
            TextBlock(text="Chapter 2", element_label="section_header", heading_level=1, page_number=2),
            TextBlock(text="Body B.", element_label="text", page_number=2),
        ]
    )
    sections = chunker.split(doc)
    assert len(sections) == 2
    assert sections[0].section_title == "Chapter 1"
    assert sections[1].section_title == "Chapter 2"


def test_consecutive_list_items_become_one_bullet_block_in_section_text():
    chunker = StructureChunker()
    doc = _parsed(
        [
            TextBlock(text="Steps", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text="Do this first", element_label="list_item", page_number=1),
            TextBlock(text="Then this", element_label="list_item", page_number=1),
        ]
    )
    sections = chunker.split(doc)
    assert len(sections) == 1
    assert "- Do this first" in sections[0].text
    assert "- Then this" in sections[0].text


def test_content_with_no_heading_still_produces_a_section():
    chunker = StructureChunker()
    doc = _parsed([TextBlock(text="Just a paragraph, no heading.", element_label="text", page_number=1)])
    sections = chunker.split(doc)
    assert len(sections) == 1
    assert sections[0].section_title is None


def test_tables_become_their_own_marked_section():
    chunker = StructureChunker()
    doc = _parsed(
        [TextBlock(text="Intro", element_label="text", page_number=1)],
        tables=[TableBlock(markdown="| a | b |", page_number=2, caption="Pricing")],
    )
    sections = chunker.split(doc)
    table_sections = [s for s in sections if s.is_table]
    assert len(table_sections) == 1
    assert table_sections[0].section_title == "Pricing"
    assert table_sections[0].page_number == 2


def test_blank_blocks_do_not_produce_empty_sections():
    chunker = StructureChunker()
    doc = _parsed([TextBlock(text="   ", element_label="text", page_number=1)])
    sections = chunker.split(doc)
    assert sections == []
