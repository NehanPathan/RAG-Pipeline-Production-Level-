from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.models import OCRPageResult, OCRResult
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class PaddleOCRProvider(OCRProvider):
    """PaddleOCR-backed OCRProvider.

    Interface-complete and registered in `registry.py`, but not wired for
    production use in this deployment: PaddleOCR depends on `paddlepaddle`, a
    full deep-learning framework that is heavy on CPU-only hosts, and this
    environment has no GPU (see the BGE reranker's 115s CPU timeout,
    documented in `docs/HOW_IT_WORKS.md` Stage D, which is why this project
    already defaults reranking to a non-ML passthrough). Switching this on
    later is a one-line config change (`OCR_PROVIDER=paddle`) once GPU is
    available -- no call-site changes anywhere else in the ingestion
    pipeline, since it satisfies the same `OCRProvider` contract as
    `TesseractProvider`.

    Strengths: strong multilingual + rotated/curved-text detection, actively
    maintained, built-in table-region detection, generally higher accuracy
    than Tesseract on noisy real-world scans.
    Weaknesses: large dependency footprint (paddlepaddle + paddleocr, several
    hundred MB, plus a first-run model download), materially slower than
    Tesseract on CPU-only hardware, less mature Windows support than Linux.
    Hardware requirements: GPU strongly recommended for practical latency;
    CPU inference runs but at multi-second-per-page speeds vs. Tesseract's
    sub-second/page, per PaddleOCR's own published benchmarks.
    Supported document types: images and scanned PDFs (rendered per-page,
    same page-loading approach as `TesseractProvider`).
    """

    def __init__(self, model_lang: str = "en") -> None:
        self._model_lang = model_lang
        self._ocr: Any = None

    @property
    def name(self) -> str:
        return "paddle"

    async def recognize(
        self, file_path: Path, language: str = "en", pages: list[int] | None = None
    ) -> OCRResult:
        # `pages` (per-page OCR, see OCRDetector) is accepted for interface
        # parity but not applied here -- unwired for production use, see
        # class docstring.
        return await asyncio.to_thread(self._recognize_sync, file_path, language)

    def _recognize_sync(self, file_path: Path, language: str) -> OCRResult:
        from paddleocr import PaddleOCR

        if self._ocr is None:
            self._ocr = PaddleOCR(use_angle_cls=True, lang=self._model_lang)

        start = time.perf_counter()
        result = self._ocr.ocr(str(file_path), cls=True) or []
        pages = [self._parse_page(i, page) for i, page in enumerate(result, start=1)]
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "paddle_ocr_done", file=str(file_path), pages=len(pages), elapsed_ms=round(elapsed_ms, 1)
        )
        return OCRResult(engine=self.name, language=language, pages=pages, processing_time_ms=elapsed_ms)

    def _parse_page(self, page_number: int, page_result: list) -> OCRPageResult:
        texts: list[str] = []
        confidences: list[float] = []
        for _, (text, confidence) in page_result or []:
            texts.append(text)
            confidences.append(confidence)
        return OCRPageResult(
            page_number=page_number,
            text=" ".join(texts),
            confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        )
