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


class TestSoftFilterRelaxation:
    """Regression: FilterGenerator infers `domain`/`tags` from the wording of a
    question with no knowledge of what exists in the corpus. Applied as hard
    AND constraints, one wrong guess makes the whole corpus invisible — so
    retrieval retries without them. It must never retry without the access
    controls."""

    def test_detects_soft_filters(self):
        assert MetadataFilterSpec(domain="Sales").has_soft_filters is True
        assert MetadataFilterSpec(tags=["refund"]).has_soft_filters is True
        assert MetadataFilterSpec(file_type="pdf").has_soft_filters is True

    def test_security_only_filters_are_not_soft(self):
        spec = MetadataFilterSpec(
            user_id=uuid.uuid4(), sensitivity_in=["public"], document_ids=[uuid.uuid4()]
        )
        assert spec.has_soft_filters is False

    def test_security_only_drops_the_precision_hints(self):
        spec = MetadataFilterSpec(
            domain="Sales", tags=["refund", "eu"], file_type="pdf"
        )
        relaxed = spec.security_only()
        assert relaxed.domain is None
        assert relaxed.tags is None
        assert relaxed.file_type is None

    def test_security_only_keeps_the_access_controls(self):
        """The property that matters: relaxing precision must never relax
        tenancy or clearance."""
        user_id, doc_id = uuid.uuid4(), uuid.uuid4()
        spec = MetadataFilterSpec(
            user_id=user_id,
            sensitivity_in=["public", "internal"],
            document_ids=[doc_id],
            domain="Sales",
            tags=["refund"],
        )
        relaxed = spec.security_only()

        assert relaxed.user_id == user_id
        assert relaxed.sensitivity_in == ["public", "internal"]
        assert relaxed.document_ids == [doc_id]

    def test_relaxed_spec_still_carries_clearance_into_both_backends(self):
        relaxed = MetadataFilterSpec(
            sensitivity_in=["public"], domain="Sales"
        ).security_only()
        assert relaxed.to_vector_filter().sensitivity_in == ["public"]
        assert relaxed.to_bm25_filter().sensitivity_in == ["public"]

    def test_security_only_returns_a_copy(self):
        spec = MetadataFilterSpec(domain="Sales")
        relaxed = spec.security_only()
        assert relaxed is not spec
        assert spec.domain == "Sales", "the original must be left intact"
