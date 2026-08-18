from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.models import BoundingBox, OCRPageResult, OCRResult, OCRWord
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

PageLoader = Callable[[Path], Iterable[tuple[int, Any]]]
ImageToDataFn = Callable[[Any, str], dict]

DEFAULT_LANG_MAP = {"en": "eng", "fr": "fra", "de": "deu", "es": "spa", "zh": "chi_sim"}


class TesseractProvider(OCRProvider):
    """OCR via the system `tesseract` binary (pytesseract wrapper) -- CPU-only,
    no GPU required, the default local engine for this deployment (see
    docs/HOW_IT_WORKS.md Stage D for why this environment is CPU-only).

    `page_loader` (PDF/image -> per-page image objects) and `image_to_data`
    (image -> pytesseract's raw word-level dict) are injected rather than
    importing pytesseract/PIL/pdf2image directly here, so unit tests
    substitute fakes with the same call shape instead of requiring the real
    Tesseract binary and poppler system packages to be installed -- the same
    pattern `BGEReranker` uses for its injected `CrossEncoder`.
    """

    def __init__(
        self,
        page_loader: PageLoader,
        image_to_data: ImageToDataFn,
        lang_map: dict[str, str] | None = None,
    ) -> None:
        self._page_loader = page_loader
        self._image_to_data = image_to_data
        self._lang_map = lang_map or DEFAULT_LANG_MAP

    @property
    def name(self) -> str:
        return "tesseract"

    async def recognize(
        self, file_path: Path, language: str = "en", pages: list[int] | None = None
    ) -> OCRResult:
        return await asyncio.to_thread(self._recognize_sync, file_path, language, pages)

    def _recognize_sync(
        self, file_path: Path, language: str, pages: list[int] | None = None
    ) -> OCRResult:
        lang_code = self._lang_map.get(language, "eng")
        start = time.perf_counter()

        wanted = set(pages) if pages is not None else None
        page_results: list[OCRPageResult] = [
            self._parse_page(page_number, self._image_to_data(image, lang_code))
            for page_number, image in self._page_loader(file_path)
            if wanted is None or page_number in wanted
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "tesseract_ocr_done",
            file=str(file_path),
            pages=len(page_results),
            elapsed_ms=round(elapsed_ms, 1),
        )
        return OCRResult(
            engine=self.name,
            language=language,
            pages=page_results,
            processing_time_ms=elapsed_ms,
        )

    def _parse_page(self, page_number: int, data: dict) -> OCRPageResult:
        words: list[OCRWord] = []
        confidences: list[float] = []
        text_parts: list[str] = []

        for i, text in enumerate(data.get("text", [])):
            if not text.strip():
                continue
            conf_raw = float(data["conf"][i])
            conf = conf_raw / 100.0 if conf_raw >= 0 else 0.0
            confidences.append(conf)
            text_parts.append(text)
            words.append(
                OCRWord(
                    text=text,
                    confidence=conf,
                    bbox=BoundingBox(
                        x0=float(data["left"][i]),
                        y0=float(data["top"][i]),
                        x1=float(data["left"][i]) + float(data["width"][i]),
                        y1=float(data["top"][i]) + float(data["height"][i]),
                    ),
                )
            )

        return OCRPageResult(
            page_number=page_number,
            text=" ".join(text_parts),
            confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            words=words,
        )
