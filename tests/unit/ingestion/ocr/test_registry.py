import pytest

from src.config import Settings
from src.ingestion.ocr.paddle_provider import PaddleOCRProvider
from src.ingestion.ocr.registry import get_ocr_provider
from src.ingestion.ocr.tesseract_provider import TesseractProvider
from src.ingestion.ocr.unlimited_provider import UnlimitedOCRProvider


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_default_provider_is_tesseract():
    provider = get_ocr_provider(_settings())
    assert isinstance(provider, TesseractProvider)
    assert provider.name == "tesseract"


def test_paddle_provider_selected_by_config():
    provider = get_ocr_provider(_settings(ocr_provider="paddle", paddle_ocr_lang="fr"))
    assert isinstance(provider, PaddleOCRProvider)
    assert provider.name == "paddle"


def test_baidu_unlimited_provider_selected_by_config():
    provider = get_ocr_provider(
        _settings(
            ocr_provider="baidu_unlimited",
            baidu_ocr_api_key="key",
            baidu_ocr_secret_key="secret",
        )
    )
    assert isinstance(provider, UnlimitedOCRProvider)
    assert provider.name == "baidu_unlimited"


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="Unsupported OCR provider"):
        get_ocr_provider(_settings(ocr_provider="nonexistent"))
