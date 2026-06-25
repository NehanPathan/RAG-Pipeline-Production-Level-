import uuid
from datetime import date

from src.domain.repositories.search_repository import BM25SearchFilter
from src.domain.repositories.vector_repository import VectorSearchFilter
from src.domain.value_objects.metadata_filter import MetadataFilterSpec


def test_to_vector_filter_maps_supported_fields():
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    spec = MetadataFilterSpec(
        user_id=user_id, domain="HR", tags=["policy"], file_type="pdf", document_ids=[doc_id]
    )

    result = spec.to_vector_filter()

    assert isinstance(result, VectorSearchFilter)
    assert result.user_id == user_id
    assert result.domain == "HR"
    assert result.tags == ["policy"]
    assert result.file_type == "pdf"
    assert result.document_ids == [doc_id]


def test_to_bm25_filter_maps_supported_fields():
    spec = MetadataFilterSpec(domain="Legal", tags=["contract"])

    result = spec.to_bm25_filter()

    assert isinstance(result, BM25SearchFilter)
    assert result.domain == "Legal"
    assert result.tags == ["contract"]


def test_date_and_custom_fields_do_not_break_adapters():
    spec = MetadataFilterSpec(
        domain="HR",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 6, 1),
        custom={"owner": "legal"},
    )

    vector_filter = spec.to_vector_filter()
    bm25_filter = spec.to_bm25_filter()

    assert vector_filter.domain == "HR"
    assert bm25_filter.domain == "HR"
    assert not hasattr(vector_filter, "date_from")
    assert not hasattr(bm25_filter, "date_from")
