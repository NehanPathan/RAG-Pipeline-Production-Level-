import uuid

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.value_objects.provenance import PRECISION_BLOCK, SPACE_SHEET, Region
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.search.elasticsearch.repository import INDEX_MAPPINGS
from src.infrastructure.serialization.chunk_payload import chunk_to_payload, payload_to_chunk


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        document_id=uuid.uuid4(),
        content="Beam B-14 is an ISMB 300 spanning 6000 mm between grids 4 and 5.",
        position=7,
        chunk_type=ChunkType.CHILD,
        token_count=19,
        parent_chunk_id=uuid.uuid4(),
        chunk_metadata=ChunkMetadata(
            page_number=3,
            section="Beam Schedule",
            section_title="4.2 Primary Framing",
            heading_level=2,
            contains_table=False,
            content_kind="scanned_drawing",
        ),
        user_id=uuid.uuid4(),
        domain="Engineering",
        tags=["ismb-300", "beam-schedule"],
        file_type="pdf",
        document_name="S-101 Rev C.pdf",
        sensitivity=Sensitivity.CONFIDENTIAL,
    )


def test_round_trip_preserves_every_field():
    original = _chunk()

    restored = payload_to_chunk(chunk_to_payload(original), original.id)

    assert restored.id == original.id
    assert restored.document_id == original.document_id
    assert restored.content == original.content
    assert restored.position == original.position
    assert restored.chunk_type == original.chunk_type
    assert restored.token_count == original.token_count
    assert restored.parent_chunk_id == original.parent_chunk_id
    assert restored.user_id == original.user_id
    assert restored.domain == original.domain
    assert restored.tags == original.tags
    assert restored.file_type == original.file_type
    assert restored.document_name == original.document_name
    assert restored.sensitivity == original.sensitivity
    assert restored.chunk_metadata == original.chunk_metadata


def test_section_title_and_heading_level_survive():
    """Regression: both were persisted to Postgres but omitted from the
    Qdrant and Elasticsearch payloads, so every retrieved chunk had them as
    None and citations lost their section on the Docling path."""
    original = _chunk()

    payload = chunk_to_payload(original)

    assert payload["section_title"] == "4.2 Primary Framing"
    assert payload["heading_level"] == 2
    assert payload_to_chunk(payload, original.id).chunk_metadata.section_title == (
        "4.2 Primary Framing"
    )


def test_every_payload_key_is_mapped_in_elasticsearch():
    """The mapping and the serializer must not drift apart -- an unmapped key
    is dynamically typed by Elasticsearch, which is how a keyword field
    silently becomes analysed text and stops matching term filters."""
    mapped = set(INDEX_MAPPINGS["mappings"]["properties"])

    unmapped = set(chunk_to_payload(_chunk())) - mapped

    assert unmapped == set(), f"payload keys with no explicit mapping: {sorted(unmapped)}"


def test_legacy_payload_without_sensitivity_fails_closed():
    payload = chunk_to_payload(_chunk())
    del payload["sensitivity"]

    restored = payload_to_chunk(payload, uuid.uuid4())

    assert restored.sensitivity == Sensitivity.INTERNAL


def test_missing_optional_keys_do_not_raise():
    """Points written before a field existed simply lack the key."""
    original = _chunk()
    payload = {
        "document_id": str(original.document_id),
        "content": original.content,
    }

    restored = payload_to_chunk(payload, original.id)

    assert restored.content == original.content
    assert restored.chunk_metadata.section_title is None
    assert restored.tags == []
    assert restored.user_id is None


def test_content_kind_survives_the_round_trip():
    """How the text was obtained has to reach the search backends.

    It is the difference between a dimension read from a DXF and one OCR'd
    off a scanned sheet, and an answer quoting a measurement should be able
    to say which it had. A payload-only field would also be erased by the
    first run of scripts/reindex_chunks.py, which rebuilds both backends
    from Postgres -- hence the matching column in migration 0007.
    """
    original = _chunk()

    payload = chunk_to_payload(original)

    assert payload["content_kind"] == "scanned_drawing"
    assert payload_to_chunk(payload, original.id).chunk_metadata.content_kind == "scanned_drawing"


def test_content_kind_is_indexed_as_a_keyword_not_analysed_text():
    """It is filtered on exactly, never searched.

    Analysed text would split "scanned_drawing" into tokens and stop the
    term filter matching at all.
    """
    assert INDEX_MAPPINGS["mappings"]["properties"]["content_kind"] == {"type": "keyword"}


def test_a_chunk_ingested_before_classification_existed_has_no_kind():
    """NULL means "unknown", and must not be guessed at.

    Asserting prose for every pre-existing chunk would mislabel every
    drawing already in the corpus.
    """
    payload = chunk_to_payload(_chunk())
    del payload["content_kind"]

    assert payload_to_chunk(payload, uuid.uuid4()).chunk_metadata.content_kind is None


def test_regions_survive_the_round_trip():
    """A highlight rectangle that only lives in memory is not a citation
    anyone can check after the answer is streamed."""
    original = _chunk()
    original.chunk_metadata.regions = [
        Region(page_number=3, x0=0.1, y0=0.2, x1=0.3, y1=0.25, space=SPACE_SHEET),
        Region(page_number=3, x0=0.6, y0=0.7, x1=0.9, y1=0.8, space=SPACE_SHEET),
    ]
    original.chunk_metadata.region_precision = PRECISION_BLOCK

    rebuilt = payload_to_chunk(chunk_to_payload(original), original.id)

    assert rebuilt.chunk_metadata.regions == original.chunk_metadata.regions
    assert rebuilt.chunk_metadata.region_precision == PRECISION_BLOCK


def test_regions_are_stored_but_not_indexed_in_elasticsearch():
    """Six float subfields per region per chunk is a mapping explosion that
    buys nothing -- nothing queries a rectangle."""
    assert INDEX_MAPPINGS["mappings"]["properties"]["regions"] == {
        "type": "object",
        "enabled": False,
    }


def test_a_chunk_ingested_before_regions_existed_round_trips():
    """Rows written before this field have no `regions` key, and the honest
    value is "not captured" rather than a rectangle around nothing."""
    payload = chunk_to_payload(_chunk())
    del payload["regions"]
    del payload["region_precision"]

    rebuilt = payload_to_chunk(payload, uuid.uuid4())

    assert rebuilt.chunk_metadata.regions == []
    assert rebuilt.chunk_metadata.region_precision is None


def test_a_malformed_region_is_dropped_rather_than_raising():
    payload = chunk_to_payload(_chunk())
    payload["regions"] = [{"page_number": 1, "x0": "nonsense"}]

    rebuilt = payload_to_chunk(payload, uuid.uuid4())

    assert rebuilt.chunk_metadata.regions == []


def test_layers_survive_the_round_trip_and_are_indexed():
    """The point of denormalising: a post-filter can only narrow what top_k
    already returned, so a layer outside the first page of hits is invisible
    to it."""
    original = _chunk()
    original.chunk_metadata.layers = ["S-TEXT", "S-SECT_STEEL_THRU"]

    payload = chunk_to_payload(original)
    rebuilt = payload_to_chunk(payload, original.id)

    assert payload["layers"] == ["S-SECT_STEEL_THRU", "S-TEXT"], "sorted, deduplicated"
    assert set(rebuilt.chunk_metadata.layers) == {"S-TEXT", "S-SECT_STEEL_THRU"}
    assert INDEX_MAPPINGS["mappings"]["properties"]["layers"] == {"type": "keyword"}


def test_layers_are_a_qdrant_payload_index():
    """An unindexed payload field means Qdrant full-scans for it forever, with
    correct results and silently terrible latency."""
    from src.infrastructure.vector_store.qdrant.repository import INDEXED_PAYLOAD_FIELDS

    assert "layers" in INDEXED_PAYLOAD_FIELDS


def test_a_chunk_with_no_layers_round_trips_as_empty():
    """Prose has no layers, and an invented one would make the filter lie."""
    rebuilt = payload_to_chunk(chunk_to_payload(_chunk()), uuid.uuid4())
    assert rebuilt.chunk_metadata.layers == []
