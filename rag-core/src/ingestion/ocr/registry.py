from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import Settings
from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.paddle_provider import PaddleOCRProvider
from src.ingestion.ocr.tesseract_provider import TesseractProvider
from src.ingestion.ocr.unlimited_provider import UnlimitedOCRProvider


def get_ocr_provider(settings: Settings) -> OCRProvider:
    """Select and construct the configured OCRProvider.

    - "tesseract": local, CPU-only, the default (pytesseract + system
      `tesseract-ocr` binary; PDFs are rendered to per-page images via
      `pdf2image`/poppler first)
    - "paddle": local PaddleOCR -- interface-complete but not wired for
      production use in this environment yet (see `PaddleOCRProvider`'s
      docstring: heavy CPU-only footprint, GPU recommended)
    - "baidu_unlimited": Baidu's cloud General Text Recognition (Unlimited)
      API -- interface-complete but untested (no credentials available in
      this environment)
    """
    provider = settings.ocr_provider

    if provider == "tesseract":
        return TesseractProvider(page_loader=_load_pages, image_to_data=_image_to_data)

    if provider == "paddle":
        return PaddleOCRProvider(model_lang=settings.paddle_ocr_lang)

    if provider == "baidu_unlimited":
        return UnlimitedOCRProvider(
            api_key=settings.baidu_ocr_api_key,
            secret_key=settings.baidu_ocr_secret_key,
        )

    raise ValueError(f"Unsupported OCR provider: {provider!r}")


def _load_pages(file_path: Path) -> list[tuple[int, Any]]:
    """Real page loader wired into TesseractProvider: renders a PDF's pages
    to images via pdf2image/poppler, or opens a single image file directly."""
    if file_path.suffix.lower() == ".pdf":
        from pdf2image import convert_from_path

        return list(enumerate(convert_from_path(str(file_path)), start=1))

    from PIL import Image

    return [(1, Image.open(file_path))]


def _image_to_data(image: Any, lang: str) -> dict:
    """Real pytesseract call wired into TesseractProvider.

    Imports `unstructured_pytesseract`, NOT `pytesseract` -- confirmed live
    (a real `import pytesseract` raises `ModuleNotFoundError` in this
    environment) that `unstructured[all-docs]` installs its OCR dependency
    under the top-level module name `unstructured_pytesseract`, not `pytesseract`
    as originally assumed in Module 1. It's API-compatible (same
    `image_to_data`/`Output`/`get_tesseract_version`), just a different
    import path -- confirmed directly against the installed package before
    fixing this, not assumed. See docs/architecture/12_phase4a_design_review.md.
    """
    import unstructured_pytesseract as pytesseract

    return pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
