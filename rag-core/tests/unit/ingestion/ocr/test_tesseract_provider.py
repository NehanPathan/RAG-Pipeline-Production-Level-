from pathlib import Path

import pytest

from src.ingestion.ocr.tesseract_provider import TesseractProvider

FAKE_TESSERACT_DATA = {
    "text": ["", "Hello", "world", ""],
    "conf": [-1, 95.5, 80.0, -1],
    "left": [0, 10, 60, 0],
    "top": [0, 5, 5, 0],
    "width": [0, 40, 45, 0],
    "height": [0, 12, 12, 0],
}


def fake_page_loader(file_path: Path):
    return [(1, "fake-image-1"), (2, "fake-image-2"), (3, "fake-image-3")]


def fake_image_to_data(image, lang):
    assert lang == "eng"
    return FAKE_TESSERACT_DATA


@pytest.fixture
def provider() -> TesseractProvider:
    return TesseractProvider(page_loader=fake_page_loader, image_to_data=fake_image_to_data)


async def test_recognize_produces_one_page_result_per_loaded_page(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"), language="en")
    assert result.engine == "tesseract"
    assert len(result.pages) == 3
    assert result.pages[0].page_number == 1
    assert result.pages[1].page_number == 2


async def test_recognize_filters_to_requested_pages_only(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"), pages=[2])
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 2


async def test_recognize_with_pages_none_processes_everything(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"), pages=None)
    assert len(result.pages) == 3


async def test_recognize_joins_non_empty_words_into_text(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"))
    assert result.pages[0].text == "Hello world"


async def test_recognize_skips_blank_tokens_in_bounding_boxes(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"))
    assert len(result.pages[0].words) == 2
    assert result.pages[0].words[0].text == "Hello"
    assert result.pages[0].words[0].bbox.x0 == 10.0
    assert result.pages[0].words[0].bbox.x1 == 50.0


async def test_recognize_averages_confidence_ignoring_negative_conf(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"))
    # (0.955 + 0.80) / 2 = 0.8775
    assert result.pages[0].confidence == pytest.approx(0.8775)


async def test_recognize_falls_back_to_english_for_unknown_language(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"), language="xx")
    assert result.language == "xx"
    # fake_image_to_data asserts lang == "eng" (the DEFAULT_LANG_MAP fallback)


async def test_recognize_records_processing_time(provider):
    result = await provider.recognize(Path("/tmp/test.pdf"))
    assert result.processing_time_ms >= 0
