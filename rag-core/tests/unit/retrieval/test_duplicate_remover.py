import uuid

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk
from src.retrieval.fusers.duplicate_remover import DuplicateRemover


def _fused(content, rrf_score, chunk_id=None):
    return FusedChunk(
        chunk=DocumentChunk(
            id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
        ),
        rrf_score=rrf_score,
    )


def test_remove_keeps_one_copy_of_exact_duplicate_content():
    a = _fused("same text", rrf_score=0.01)
    b = _fused("same text", rrf_score=0.05)
    remover = DuplicateRemover()

    result = remover.remove([a, b])

    assert len(result) == 1
    assert result[0].rrf_score == 0.05


def test_remove_keeps_distinct_content():
    a = _fused("text one", rrf_score=0.01)
    b = _fused("text two", rrf_score=0.02)
    remover = DuplicateRemover()

    result = remover.remove([a, b])

    assert len(result) == 2


def test_remove_reassigns_ranks_after_dedup():
    a = _fused("dup", rrf_score=0.01)
    b = _fused("dup", rrf_score=0.05)
    c = _fused("unique", rrf_score=0.03)
    remover = DuplicateRemover()

    result = remover.remove([a, b, c])

    assert [r.rank for r in result] == [1, 2]
    assert result[0].rrf_score == 0.05
    assert result[1].rrf_score == 0.03


def test_remove_empty_list_returns_empty():
    assert DuplicateRemover().remove([]) == []
