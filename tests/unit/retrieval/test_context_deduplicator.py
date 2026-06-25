import uuid

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.context_deduplicator import ContextDeduplicator


def _reranked(content):
    chunk = DocumentChunk(document_id=uuid.uuid4(), content=content, position=0)
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    return RerankedChunk(fused=fused, rerank_score=0.9)


def test_deduplicate_drops_chunk_contained_in_earlier_kept_chunk():
    parent = _reranked("The refund policy allows returns within 30 days of purchase for any reason.")
    child = _reranked("returns within 30 days")
    deduplicator = ContextDeduplicator()

    result = deduplicator.deduplicate([parent, child])

    assert result == [parent]


def test_deduplicate_keeps_distinct_chunks():
    a = _reranked("first chunk")
    b = _reranked("second chunk")
    deduplicator = ContextDeduplicator()

    result = deduplicator.deduplicate([a, b])

    assert result == [a, b]


def test_deduplicate_keeps_larger_chunk_arriving_after_smaller_one():
    # smaller chunk ranked first, larger one (which contains it) ranked second
    # -- the larger one is NOT dropped, since it adds new surrounding context
    small = _reranked("returns within 30 days")
    large = _reranked("The refund policy allows returns within 30 days of purchase for any reason.")
    deduplicator = ContextDeduplicator()

    result = deduplicator.deduplicate([small, large])

    assert result == [small, large]


def test_deduplicate_empty_list_returns_empty():
    assert ContextDeduplicator().deduplicate([]) == []
