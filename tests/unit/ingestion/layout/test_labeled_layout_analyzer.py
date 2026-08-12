from pathlib import Path

from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.base import ImageRef, RawDocument, TableBlock, TextBlock


def _doc(blocks: list[TextBlock], tables=None, image_refs=None) -> RawDocument:
    return RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=blocks,
        tables=tables or [],
        image_refs=image_refs or [],
        page_count=1,
    )


def test_titles_and_section_headers_become_headings():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Document Title", element_label="title", page_number=1),
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text="Section 1.1", element_label="section_header", heading_level=2, page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert [h.text for h in layout.headings] == ["Document Title", "Chapter 1", "Section 1.1"]
    assert [h.level for h in layout.headings] == [0, 1, 2]


def test_outline_nests_headings_by_level():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Title", element_label="title", page_number=1),
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text="Section 1.1", element_label="section_header", heading_level=2, page_number=1),
            TextBlock(text="Chapter 2", element_label="section_header", heading_level=1, page_number=2),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.outline) == 1
    root = layout.outline[0]
    assert root.title == "Title"
    assert len(root.children) == 2
    assert root.children[0].title == "Chapter 1"
    assert root.children[0].children[0].title == "Section 1.1"
    assert root.children[1].title == "Chapter 2"


def test_consecutive_list_items_grouped_into_one_list_block():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Intro", element_label="text", page_number=1),
            TextBlock(text="First item", element_label="list_item", page_number=1),
            TextBlock(text="Second item", element_label="list_item", page_number=1),
            TextBlock(text="Third item", element_label="list_item", page_number=1),
            TextBlock(text="Outro", element_label="text", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.lists) == 1
    assert layout.lists[0].items == ["First item", "Second item", "Third item"]


def test_two_separate_lists_are_not_merged():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Item A", element_label="list_item", page_number=1),
            TextBlock(text="Between", element_label="text", page_number=1),
            TextBlock(text="Item B", element_label="list_item", page_number=2),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.lists) == 2
    assert layout.lists[0].items == ["Item A"]
    assert layout.lists[1].items == ["Item B"]


def test_captions_and_footnotes_extracted():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Figure 1: a chart", element_label="caption", page_number=1),
            TextBlock(text="See appendix for details.", element_label="footnote", page_number=1),
            TextBlock(text="Also a footnote via flag.", is_footnote=True, page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert [c.text for c in layout.captions] == ["Figure 1: a chart"]
    assert len(layout.footnotes) == 2


def test_field_key_value_pairs_become_form_fields():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Name:", element_label="field_key", page_number=1),
            TextBlock(text="Jane Doe", element_label="field_value", page_number=1),
            TextBlock(text="Date:", element_label="field_key", page_number=1),
            TextBlock(text="2026-01-01", element_label="field_value", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.forms) == 2
    assert layout.forms[0].key == "Name:"
    assert layout.forms[0].value == "Jane Doe"


def test_tables_and_figures_carried_through_unflattened():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc(
        [TextBlock(text="Text", element_label="text", page_number=1)],
        tables=[TableBlock(markdown="| a |", page_number=1, row_count=1, col_count=1, caption="Table 1")],
        image_refs=[ImageRef(caption="A picture", page_number=1)],
    )
    layout = analyzer.analyze(doc)
    assert len(layout.tables) == 1
    assert layout.tables[0].caption == "Table 1"
    assert len(layout.figures) == 1
    assert layout.figures[0].caption == "A picture"


def test_blank_text_blocks_are_ignored():
    analyzer = LabeledLayoutAnalyzer()
    doc = _doc([TextBlock(text="   ", element_label="title", page_number=1)])
    layout = analyzer.analyze(doc)
    assert layout.headings == []
