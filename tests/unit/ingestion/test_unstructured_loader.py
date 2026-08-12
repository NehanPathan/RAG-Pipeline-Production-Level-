from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.ingestion.loaders.unstructured_loader import UnstructuredLoader


def _fake_element(text: str, category: str, page_number: int = 1, category_depth=None, section=None):
    class _FakeElement:
        def __str__(self_inner):
            return text

    element = _FakeElement()
    element.__class__.__name__ = category
    element.metadata = SimpleNamespace(page_number=page_number, category_depth=category_depth, section=section)
    return element


async def test_title_mapped_to_title_label_with_depth():
    elements = [_fake_element("Refund Policy", "Title", category_depth=0)]
    with patch("unstructured.partition.auto.partition", return_value=elements):
        loader = UnstructuredLoader()
        raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "title"
    assert raw.text_blocks[0].heading_level == 0


async def test_list_item_mapped_to_list_item_label():
    elements = [_fake_element("First point", "ListItem")]
    with patch("unstructured.partition.auto.partition", return_value=elements):
        loader = UnstructuredLoader()
        raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "list_item"


async def test_narrative_text_mapped_to_text_label_with_no_heading_level():
    elements = [_fake_element("Some body copy.", "NarrativeText")]
    with patch("unstructured.partition.auto.partition", return_value=elements):
        loader = UnstructuredLoader()
        raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "text"
    assert raw.text_blocks[0].heading_level is None


async def test_table_elements_are_not_added_as_text_blocks():
    elements = [_fake_element("| a | b |", "Table")]
    with patch("unstructured.partition.auto.partition", return_value=elements):
        loader = UnstructuredLoader()
        raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks == []
    assert len(raw.tables) == 1
