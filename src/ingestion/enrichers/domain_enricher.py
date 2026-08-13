"""Adds steel-domain entities to the generic metadata enrichment.

Wraps `LLMMetadataEnricher` rather than replacing it. The base enricher
produces `summary`, `tags`, `domain` and `language`, which feed the document
list UI and `MetadataFilterSpec`; replacing it to get entities would have
broken all of that for no reason.

The placement of the output is the point. Canonical designations are
appended to `metadata.tags`, and `tags` is *already* indexed as a keyword
field in both Qdrant and Elasticsearch. So "find every drawing that
references ISMB 300" becomes a filter on an existing index rather than a new
index schema, a migration and a backfill.

Full entity records, with their provenance and attributes, go into
`custom_metadata` -- a JSONB column that already exists and was never
written to.
"""

from __future__ import annotations

from src.domain.entities.document import DocumentMetadata
from src.ingestion.enrichers.llm_enricher import LLMMetadataEnricher
from src.ingestion.extractors.drawing_identity import (
    CUSTOM_METADATA_KEY,
    extract_drawing_identity,
)
from src.ingestion.extractors.models import SteelEntityType
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Types worth promoting into the tag index. Dimensions are deliberately
# excluded: a drawing carries hundreds, they are not what anyone filters a
# corpus by, and they would swamp the keyword index with noise.
_TAGGABLE = (
    SteelEntityType.SECTION_DESIGNATION,
    SteelEntityType.STEEL_GRADE,
    SteelEntityType.BOLT_SPEC,
    SteelEntityType.DRAWING_NUMBER,
    SteelEntityType.PART_MARK,
    SteelEntityType.PROJECT_NUMBER,
)


class DomainMetadataEnricher:
    """Generic enrichment plus steel entity extraction."""

    def __init__(
        self,
        base: LLMMetadataEnricher,
        extractor: RegexSteelEntityExtractor | None = None,
        max_chars: int = 40_000,
        max_tags: int = 40,
    ) -> None:
        self._base = base
        self._extractor = extractor or RegexSteelEntityExtractor()
        self._max_chars = max_chars
        self._max_tags = max_tags

    async def enrich(
        self,
        content: str,
        file_name: str,
        parsed_document: ParsedDocument | None = None,
    ) -> DocumentMetadata:
        metadata = await self._base.enrich(content, file_name)

        # The extractor sees far more of the document than the LLM enricher
        # does (which reads the first 2000 characters). It costs nothing per
        # character, so the limit here is about bounding worst-case CPU on a
        # pathological document, not about tokens.
        result = self._extractor.extract_sync(content[: self._max_chars])
        if not result.entities:
            return metadata

        metadata.custom_metadata["steel_entities"] = result.to_dict()

        # Which drawing this document *is*, as opposed to which it mentions.
        # Computed here because this is where the extraction result still
        # exists in full -- the persisted entity dicts drop occurrences, and
        # the identity is decided by how often a number is repeated.
        identity = extract_drawing_identity(result)
        if identity is not None:
            metadata.custom_metadata[CUSTOM_METADATA_KEY] = identity.to_dict()

        existing = {t.lower() for t in metadata.tags}
        for canonical in result.canonicals(*_TAGGABLE):
            if len(metadata.tags) >= self._max_tags:
                break
            if canonical.lower() not in existing:
                metadata.tags.append(canonical)
                existing.add(canonical.lower())

        logger.info(
            "steel_entities_extracted",
            file=file_name,
            **{f"n_{k}": v for k, v in result.by_type.items()},
        )
        return metadata
