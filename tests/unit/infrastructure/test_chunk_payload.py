import uuid

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
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
