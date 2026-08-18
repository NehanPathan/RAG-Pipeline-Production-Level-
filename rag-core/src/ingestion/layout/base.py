from __future__ import annotations

from abc import ABC, abstractmethod

from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument


class LayoutAnalyzer(ABC):
    """Extracts document structure (headings, tables, figures, captions,
    lists, forms, footnotes, outline) from a loaded RawDocument, without
    flattening any of it into plain text (Part 3)."""

    @abstractmethod
    def analyze(self, raw_document: RawDocument) -> DocumentLayout:
        ...
