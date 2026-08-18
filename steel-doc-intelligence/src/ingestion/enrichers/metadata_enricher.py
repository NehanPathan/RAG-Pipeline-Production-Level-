from __future__ import annotations

from typing import Protocol

from src.domain.entities.document import DocumentMetadata
from src.ingestion.parsing.parsed_document import ParsedDocument


class MetadataEnricher(Protocol):
    """Produces document-level metadata from parsed content.

    A Protocol so the generic LLM enricher and the steel-domain enricher that
    wraps it are interchangeable at the pipeline's construction site, with no
    inheritance between them.

    `parsed_document` is optional because the generic enricher works from raw
    text alone; a domain enricher that wants layout, OCR confidence or CAD
    records can read it.
    """

    async def enrich(
        self,
        content: str,
        file_name: str,
        parsed_document: ParsedDocument | None = None,
    ) -> DocumentMetadata: ...
