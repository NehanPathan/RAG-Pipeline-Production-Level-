from pathlib import Path

from src.ingestion.loaders.image_loader import ImagePassthroughLoader


def test_supports_image_extensions():
    loader = ImagePassthroughLoader()
    assert loader.supports("image/png", ".png") is True
    assert loader.supports("application/octet-stream", ".jpg") is True
    assert loader.supports("application/pdf", ".pdf") is False


async def test_load_returns_empty_text_blocks():
    loader = ImagePassthroughLoader()
    raw = await loader.load(Path("/tmp/photo.png"))
    assert raw.text_blocks == []
    assert raw.word_count == 0
    assert raw.mime_type == "image/png"
    assert raw.loader_name == "image_passthrough"
