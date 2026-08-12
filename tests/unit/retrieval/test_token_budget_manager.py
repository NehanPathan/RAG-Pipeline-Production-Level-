import uuid
from unittest.mock import MagicMock

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.token_budget_manager import TokenBudgetManager


def _compressed(content):
    chunk = DocumentChunk(document_id=uuid.uuid4(), content=content, position=0)
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    reranked = RerankedChunk(fused=fused, rerank_score=0.9)
    return CompressedChunk(reranked=reranked, compressed_content=content)


def _counter(tokens_per_call):
    counter = MagicMock()
    counter.count = MagicMock(side_effect=tokens_per_call)
    return counter


def test_select_keeps_chunks_within_budget():
    chunks = [_compressed("a"), _compressed("b"), _compressed("c")]
    manager = TokenBudgetManager(token_counter=_counter([10, 10, 10]), max_tokens=25)

    result = manager.select(chunks)

    assert len(result) == 2


def test_select_stops_at_first_chunk_that_would_overflow():
    chunks = [_compressed("a"), _compressed("b"), _compressed("c")]
    counter = _counter([10, 100, 5])  # second chunk overflows; third never checked
    manager = TokenBudgetManager(token_counter=counter, max_tokens=50)

    result = manager.select(chunks)

    assert len(result) == 1
    assert counter.count.call_count == 2


def test_select_sets_token_count_on_kept_chunks():
    chunks = [_compressed("a")]
    manager = TokenBudgetManager(token_counter=_counter([15]), max_tokens=100)

    result = manager.select(chunks)

    assert result[0].token_count == 15


def test_select_empty_list_returns_empty():
    manager = TokenBudgetManager(token_counter=_counter([]), max_tokens=100)

    assert manager.select([]) == []
