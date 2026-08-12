from __future__ import annotations

from typing import Protocol

from src.ingestion.extractors.models import EntityExtractionResult


class EntityExtractor(Protocol):
    """Extracts domain entities from a block of text.

    A Protocol rather than an ABC so the regex pass, the LLM pass and any
    later vision pass are interchangeable without inheriting from a common
    base -- the same reason `ChunkingStrategy` is one.
    """

    async def extract(self, text: str, page_number: int | None = None) -> EntityExtractionResult:
        ...
