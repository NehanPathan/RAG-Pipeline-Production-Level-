from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.ingestion.loaders.docling_loader import DoclingLoader


def _fake_label(value: str):
    return SimpleNamespace(value=value)


def _fake_item(text: str, label: str, page_no: int, level=None, bbox=None):
    prov = SimpleNamespace(page_no=page_no, bbox=bbox)
    item = SimpleNamespace(text=text, label=_fake_label(label), prov=[prov])
    if level is not None:
        item.level = level
    return item


def _fake_bbox():
    return SimpleNamespace(l=1.0, t=2.0, r=3.0, b=4.0)


async def test_populates_element_label_and_heading_level():
    fake_doc = SimpleNamespace(
        pages={1: object()},
        texts=[
            _fake_item("Document Title", "title", page_no=1),
            _fake_item("Chapter 1", "section_header", page_no=1, level=1, bbox=_fake_bbox()),
            _fake_item("Body text.", "text", page_no=1),
        ],
        tables=[],
        pictures=[],
    )
    fake_result = SimpleNamespace(document=fake_doc)
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result

    with patch("docling.document_converter.DocumentConverter", return_value=fake_converter):
        loader = DoclingLoader()
        raw = await loader.load(Path("/tmp/test.pdf"))

    labels = [b.element_label for b in raw.text_blocks]
    assert labels == ["title", "section_header", "text"]
    assert raw.text_blocks[1].heading_level == 1
    assert raw.text_blocks[1].bbox.x0 == 1.0
    assert raw.text_blocks[1].bbox.y1 == 4.0


async def test_footnote_label_sets_is_footnote_flag():
    fake_doc = SimpleNamespace(
        pages={1: object()},
        texts=[_fake_item("See note 1.", "footnote", page_no=1)],
        tables=[],
        pictures=[],
    )
    fake_result = SimpleNamespace(document=fake_doc)
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result

    with patch("docling.document_converter.DocumentConverter", return_value=fake_converter):
        loader = DoclingLoader()
        raw = await loader.load(Path("/tmp/test.pdf"))

    assert raw.text_blocks[0].is_footnote is True


async def test_items_without_prov_are_skipped():
    fake_doc = SimpleNamespace(
        pages={},
        texts=[SimpleNamespace(text="orphan", label=_fake_label("text"), prov=[])],
        tables=[],
        pictures=[],
    )
    fake_result = SimpleNamespace(document=fake_doc)
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result

    with patch("docling.document_converter.DocumentConverter", return_value=fake_converter):
        loader = DoclingLoader()
        raw = await loader.load(Path("/tmp/test.pdf"))

    assert raw.text_blocks == []
