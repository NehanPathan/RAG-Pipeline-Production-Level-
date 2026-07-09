from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument
from src.ingestion.ocr.models import OCRMetadata


@dataclass
class ParsedDocument:
    """Unified output of Load + OCR (if required) + Layout analysis -- the
    one object HybridChunkingPipeline and the LLM enricher consume from this
    point on, regardless of which loader/OCR engine/layout analyzer produced
    it (Part 1's "one unified ParsedDocument object" requirement)."""

    raw: RawDocument
    ocr_metadata: OCRMetadata
    layout: DocumentLayout

    @property
    def full_text(self) -> str:
        return self.raw.full_text
