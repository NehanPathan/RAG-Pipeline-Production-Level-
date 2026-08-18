from pathlib import Path

from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock


def _doc(blocks: list[TextBlock], tables=None) -> RawDocument:
    return RawDocument(
        file_path=Path("/tmp/test.txt"),
        file_name="test.txt",
        mime_type="text/plain",
        text_blocks=blocks,
        tables=tables or [],
        page_count=1,
    )


def test_short_capitalized_line_detected_as_heading():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="Refund Policy", page_number=1),
            TextBlock(text="Customers may request a refund within 30 days of purchase, per policy.", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.headings) == 1
    assert layout.headings[0].text == "Refund Policy"


def test_long_or_punctuated_lines_are_not_headings():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="this is not a heading because it is lowercase", page_number=1),
            TextBlock(text="Ends with a period.", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert layout.headings == []


def test_dash_and_numbered_list_items_detected():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="- First point", page_number=1),
            TextBlock(text="- Second point", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.lists) == 1
    assert layout.lists[0].items == ["First point", "Second point"]


def test_lists_separated_by_other_text_are_not_merged():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc(
        [
            TextBlock(text="- First point", page_number=1),
            TextBlock(text="A paragraph in between, ending with punctuation.", page_number=1),
            TextBlock(text="1) Numbered point", page_number=1),
        ]
    )
    layout = analyzer.analyze(doc)
    assert len(layout.lists) == 2
    assert layout.lists[0].items == ["First point"]
    assert layout.lists[1].items == ["Numbered point"]


def test_tables_pass_through_unchanged():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc(
        [TextBlock(text="Some text.", page_number=1)],
        tables=[TableBlock(markdown="| a |", page_number=1, row_count=1, col_count=1)],
    )
    layout = analyzer.analyze(doc)
    assert len(layout.tables) == 1


def test_outline_built_from_detected_headings():
    analyzer = HeuristicLayoutAnalyzer()
    doc = _doc([TextBlock(text="Overview", page_number=1)])
    layout = analyzer.analyze(doc)
    assert len(layout.outline) == 1
    assert layout.outline[0].title == "Overview"
