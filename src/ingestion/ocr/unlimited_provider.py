from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx

from src.ingestion.ocr.base import OCRProvider
from src.ingestion.ocr.models import OCRPageResult, OCRResult
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_RECOGNIZE_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general"


class UnlimitedOCRProvider(OCRProvider):
    """Baidu General Text Recognition (Unlimited) -- cloud OCR API.

    Interface-complete but untested in this environment: a repo-wide search
    (`baidu|unlimited`, case-insensitive) found no prior implementation to
    build on, and there are no Baidu API credentials available to exercise
    this against the live service. The strengths/weaknesses/hardware notes
    below are sourced from Baidu's public API documentation, not measured
    locally -- flagged explicitly rather than presented as verified numbers,
    per the resolved design decision in `12_phase4a_design_review.md`.

    Strengths (vendor-documented): very high accuracy on Chinese and mixed
    CJK/Latin text, generous free tier, no local model or GPU needed at all
    (pure HTTPS call, using the `httpx` client already a project dependency),
    handles rotated/skewed images natively.
    Weaknesses: network dependency and per-request latency (vendor-documented
    at roughly several hundred ms to ~1-2s per page), per-page cost beyond
    the free tier, data leaves the deployment environment entirely (a
    compliance consideration for sensitive documents), rate limits on the
    free tier.
    Hardware requirements: none locally -- an HTTPS client is the only
    requirement.
    Supported document types: JPG/PNG/BMP images and PDF (submitted per-page)
    up to Baidu's documented size/resolution limits.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._access_token: str | None = None

    @property
    def name(self) -> str:
        return "baidu_unlimited"

    async def recognize(
        self, file_path: Path, language: str = "en", pages: list[int] | None = None
    ) -> OCRResult:
        # `pages` (per-page OCR, see OCRDetector) is accepted for interface
        # parity but not applied here -- this stub sends the whole file as
        # one image and is untested/unwired regardless (see class docstring).
        start = time.perf_counter()
        token = await self._get_access_token()
        image_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")

        response = await self._http.post(
            _RECOGNIZE_URL,
            params={"access_token": token},
            data={"image": image_b64, "probability": "true"},
        )
        response.raise_for_status()
        payload = response.json()

        words_result = payload.get("words_result", [])
        text = "\n".join(w.get("words", "") for w in words_result)
        probabilities = [
            w["probability"]["average"]
            for w in words_result
            if "probability" in w
        ]
        confidence = sum(probabilities) / len(probabilities) if probabilities else 0.0
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "baidu_ocr_done",
            file=str(file_path),
            words=len(words_result),
            elapsed_ms=round(elapsed_ms, 1),
        )
        return OCRResult(
            engine=self.name,
            language=language,
            pages=[OCRPageResult(page_number=1, text=text, confidence=confidence)],
            processing_time_ms=elapsed_ms,
        )

    async def _get_access_token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        response = await self._http.post(
            _TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": self._api_key,
                "client_secret": self._secret_key,
            },
        )
        response.raise_for_status()
        self._access_token = response.json()["access_token"]
        return self._access_token
