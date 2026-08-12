import uuid

from src.domain.entities.document import ChunkMetadata, DocumentChunk, DocumentMetadata
from src.infrastructure.serialization.chunk_payload import chunk_to_payload
from src.ingestion.enrichers.domain_enricher import DomainMetadataEnricher
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor


class _FakeBaseEnricher:
    """Stands in for LLMMetadataEnricher, which needs a model."""

    def __init__(self, metadata: DocumentMetadata | None = None) -> None:
        self.metadata = metadata or DocumentMetadata(
            summary="A structural drawing.", tags=["structural"], domain="Engineering"
        )
        self.calls = 0

    async def enrich(self, content: str, file_name: str, parsed_document=None) -> DocumentMetadata:
        self.calls += 1
        return self.metadata


def _enricher(base=None) -> DomainMetadataEnricher:
    return DomainMetadataEnricher(
        base or _FakeBaseEnricher(),  # type: ignore[arg-type]
        extractor=RegexSteelEntityExtractor(),
    )


_DRAWING = (
    "DRAWING NO: S-104  REV B\n"
    "Beam B-14 is ISMB 300 in Fe 415, bolted M20 8.8."
)


class TestGenericEnrichmentIsPreserved:
    """The base enricher produces summary/tags/domain, which feed the document
    list and metadata filtering. Replacing it to get entities would have
    broken all of that."""

    async def test_the_base_enricher_still_runs(self):
        base = _FakeBaseEnricher()

        await _enricher(base).enrich(_DRAWING, "S-104.pdf")

        assert base.calls == 1

    async def test_its_summary_and_domain_survive(self):
        metadata = await _enricher().enrich(_DRAWING, "S-104.pdf")

        assert metadata.summary == "A structural drawing."
        assert metadata.domain == "Engineering"

    async def test_its_tags_are_kept_alongside_the_new_ones(self):
        metadata = await _enricher().enrich(_DRAWING, "S-104.pdf")

        assert "structural" in metadata.tags


class TestEntitiesReachTheIndex:
    async def test_designations_are_appended_to_tags(self):
        """`tags` is already a keyword field in both Qdrant and
        Elasticsearch, so this gives a filterable steel index with no new
        index schema, no migration and no backfill."""
        metadata = await _enricher().enrich(_DRAWING, "S-104.pdf")

        assert "ISMB 300" in metadata.tags
        assert "Fe 415" in metadata.tags

    async def test_full_records_go_to_custom_metadata(self):
        metadata = await _enricher().enrich(_DRAWING, "S-104.pdf")

        entities = metadata.custom_metadata["steel_entities"]
        assert entities["by_type"]["section_designation"] == 1
        assert any(e["canonical"] == "ISMB 300" for e in entities["entities"])

    async def test_dimensions_are_not_promoted_to_tags(self):
        """A drawing carries hundreds; they are not what anyone filters a
        corpus by, and they would swamp the keyword index."""
        metadata = await _enricher().enrich("Span 6000 mm, depth 300 mm.", "x.pdf")

        assert not any(tag.endswith(" mm") for tag in metadata.tags)

    async def test_tags_are_bounded(self):
        text = " ".join(f"ISMB {size}" for size in range(100, 600, 25)) * 3
        enricher = DomainMetadataEnricher(
            _FakeBaseEnricher(),  # type: ignore[arg-type]
            extractor=RegexSteelEntityExtractor(),
            max_tags=5,
        )

        metadata = await enricher.enrich(text, "x.pdf")

        assert len(metadata.tags) <= 5

    async def test_a_document_with_no_steel_content_is_left_alone(self):
        metadata = await _enricher().enrich("A memo about car parking.", "memo.pdf")

        assert "steel_entities" not in metadata.custom_metadata


class TestChunkPayload:
    def test_chunk_entities_become_a_flat_keyword_list(self):
        """What a search backend needs is something exactly matchable. The
        full records, with provenance and attributes, stay in Postgres."""
        result = RegexSteelEntityExtractor().extract_sync(_DRAWING)
        chunk = DocumentChunk(
            document_id=uuid.uuid4(),
            content=_DRAWING,
            position=0,
            chunk_metadata=ChunkMetadata(entities=[e.to_dict() for e in result.entities]),
        )

        payload = chunk_to_payload(chunk)

        assert "ISMB 300" in payload["entity_canonicals"]
        assert "M20 8.8" in payload["entity_canonicals"]
        assert all(isinstance(value, str) for value in payload["entity_canonicals"])

    def test_a_chunk_with_no_entities_yields_an_empty_list(self):
        chunk = DocumentChunk(document_id=uuid.uuid4(), content="No steel here.", position=0)

        assert chunk_to_payload(chunk)["entity_canonicals"] == []
