from __future__ import annotations

from dataclasses import dataclass, field

from src.ingestion.loaders.base import BoundingBox

__all__ = [
    "BoundingBox",
    "OCRMetadata",
    "OCRPageResult",
    "OCRResult",
    "OCRWord",
]


@dataclass
class OCRWord:
    text: str
    confidence: float
    bbox: BoundingBox | None = None


@dataclass
class OCRPageResult:
    page_number: int
    text: str
    confidence: float
    rotation: float = 0.0
    image_quality: float | None = None
    words: list[OCRWord] = field(default_factory=list)


@dataclass
class OCRResult:
    """Unified output of any OCRProvider, regardless of engine."""

    engine: str
    language: str
    pages: list[OCRPageResult] = field(default_factory=list)
    processing_time_ms: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text.strip())

    @property
    def average_confidence(self) -> float:
        confidences = [p.confidence for p in self.pages]
        return sum(confidences) / len(confidences) if confidences else 0.0


@dataclass
class OCRMetadata:
    """Document-level OCR summary persisted alongside a document -- covers
    both the "OCR ran" and "OCR was skipped" cases so downstream consumers
    (chunk validation, the Document Intelligence UI) always have one shape
    to read regardless of which branch the pipeline took."""

    engine: str
    ran: bool
    confidence: float | None = None
    processing_time_ms: float = 0.0
    language: str | None = None
    page_count: int = 0
    skipped_reason: str | None = None

    @classmethod
    def skipped(cls, reason: str) -> OCRMetadata:
        return cls(engine="none", ran=False, skipped_reason=reason)

    @classmethod
    def from_result(cls, result: OCRResult) -> OCRMetadata:
        return cls(
            engine=result.engine,
            ran=True,
            confidence=result.average_confidence,
            processing_time_ms=result.processing_time_ms,
            language=result.language,
            page_count=len(result.pages),
        )
