from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.ingestion.ocr.models import OCRResult


class OCRProvider(ABC):
    """Abstract base for all OCR engines.

    Every implementation returns the same unified `OCRResult` regardless of
    which engine produced it, so downstream code (the parsing orchestrator,
    layout analysis, chunk validation) never branches on which provider ran.
    """

    @abstractmethod
    async def recognize(
        self, file_path: Path, language: str = "en", pages: list[int] | None = None
    ) -> OCRResult:
        """`pages`: 1-indexed page numbers to recognize, or None for all
        pages (e.g. a standalone image, or a fully-scanned PDF)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
