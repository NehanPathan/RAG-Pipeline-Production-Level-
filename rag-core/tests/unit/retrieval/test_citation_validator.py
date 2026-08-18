import uuid

from src.domain.entities.conversation import Citation
from src.retrieval.answer.citation_validator import CitationValidator


def _citation(index, chunk_id=None):
    return Citation(
        index=index, source_name="doc.pdf", document_name="doc.pdf", chunk_id=chunk_id or uuid.uuid4()
    )


def test_validate_returns_only_referenced_citations():
    c1 = _citation(1)
    c2 = _citation(2)
    citations = {c1.chunk_id: c1, c2.chunk_id: c2}
    validator = CitationValidator()

    result = validator.validate("The answer is X [1].", citations)

    assert result == [c1]


def test_validate_drops_hallucinated_index_not_in_citations():
    c1 = _citation(1)
    citations = {c1.chunk_id: c1}
    validator = CitationValidator()

    result = validator.validate("The answer is X [1] and Y [9].", citations)

    assert result == [c1]


def test_validate_returns_empty_when_no_markers_present():
    c1 = _citation(1)
    citations = {c1.chunk_id: c1}
    validator = CitationValidator()

    result = validator.validate("The answer is X with no citation.", citations)

    assert result == []


def test_validate_deduplicates_repeated_markers_and_orders_by_index():
    c1 = _citation(1)
    c2 = _citation(2)
    citations = {c1.chunk_id: c1, c2.chunk_id: c2}
    validator = CitationValidator()

    result = validator.validate("X [2] and also [1] and again [2].", citations)

    assert result == [c1, c2]


def test_validate_empty_citations_dict_returns_empty():
    validator = CitationValidator()

    result = validator.validate("X [1].", {})

    assert result == []
