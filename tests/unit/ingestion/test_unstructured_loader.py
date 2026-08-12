from pathlib import Path
from types import SimpleNamespace

from src.ingestion.loaders.unstructured_loader import UnstructuredLoader


def _fake_element(text: str, category: str, page_number: int = 1, category_depth=None, section=None):
    class _FakeElement:
        def __str__(self):
            return text

    element = _FakeElement()
    element.__class__.__name__ = category
    element.metadata = SimpleNamespace(
        page_number=page_number, category_depth=category_depth, section=section
    )
    return element


def _loader(elements) -> UnstructuredLoader:
    """Inject the partition seam instead of patching the real module.

    Patching `unstructured.partition.auto.partition` required importing that
    module, which pulls in the whole Unstructured inference stack -- and on
    this platform that import segfaults the interpreter, which presented as
    the entire test suite hanging with no attributable failure.
    """
    return UnstructuredLoader(partition=lambda **_kwargs: elements)


async def test_title_mapped_to_title_label_with_depth():
    loader = _loader([_fake_element("Refund Policy", "Title", category_depth=0)])

    raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "title"
    assert raw.text_blocks[0].heading_level == 0


async def test_list_item_mapped_to_list_item_label():
    loader = _loader([_fake_element("First point", "ListItem")])

    raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "list_item"


async def test_narrative_text_mapped_to_text_label_with_no_heading_level():
    loader = _loader([_fake_element("Some body copy.", "NarrativeText")])

    raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks[0].element_label == "text"
    assert raw.text_blocks[0].heading_level is None


async def test_table_elements_are_not_added_as_text_blocks():
    loader = _loader([_fake_element("| a | b |", "Table")])

    raw = await loader.load(Path("/tmp/test.html"))

    assert raw.text_blocks == []
    assert len(raw.tables) == 1


async def test_real_partition_is_not_imported_when_one_is_injected():
    """The seam exists so a unit test never touches the heavy import."""
    import sys

    loader = _loader([_fake_element("Body.", "NarrativeText")])
    await loader.load(Path("/tmp/test.html"))

    assert "unstructured.partition.auto" not in sys.modules
