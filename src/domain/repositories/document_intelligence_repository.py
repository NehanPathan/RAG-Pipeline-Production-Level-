from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from src.domain.value_objects.document_intelligence import DocumentIntelligenceSummary


class DocumentIntelligenceRepository(ABC):
    """Persists/reads the document-level OCR/layout/embedding summary (Part
    7). Deliberately takes/returns only the domain-level
    `DocumentIntelligenceSummary` -- never `ParsedDocument`/`DocumentChunk`
    (ingestion-layer types) -- so this port stays in the domain layer
    without depending on an outer layer. `DocumentIntelligenceRecorder`
    (src/ingestion/parsing/) is the adapter that builds a summary from a
    ParsedDocument and calls this.
    """

    @abstractmethod
    async def save(self, summary: DocumentIntelligenceSummary) -> None: ...

    @abstractmethod
    async def get_by_document(self, document_id: uuid.UUID) -> DocumentIntelligenceSummary | None: ...
