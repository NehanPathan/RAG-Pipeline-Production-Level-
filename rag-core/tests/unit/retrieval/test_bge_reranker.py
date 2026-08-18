import uuid
from unittest.mock import MagicMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk
from src.retrieval.rerankers.bge_reranker import BGEReranker


def _fused(content, chunk_id=None):
    return FusedChunk(
        chunk=DocumentChunk(
            id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
        ),
        rrf_score=0.01,
    )


@pytest.fixture
def cross_encoder():
    encoder = MagicMock()
    encoder.predict = MagicMock(return_value=[0.2, 0.9, 0.5])
    return encoder


@pytest.fixture
def reranker(cross_encoder):
    return BGEReranker(cross_encoder=cross_encoder, model_name="BAAI/bge-reranker-large")


@pytest.mark.asyncio
async def test_rerank_orders_by_score_descending(reranker):
    candidates = [_fused("a"), _fused("b"), _fused("c")]

    result = await reranker.rerank("query", candidates, top_n=3)

    assert [r.rerank_score for r in result] == [0.9, 0.5, 0.2]
    assert result[0].fused.chunk.content == "b"


@pytest.mark.asyncio
async def test_rerank_truncates_to_top_n(reranker):
    candidates = [_fused("a"), _fused("b"), _fused("c")]

    result = await reranker.rerank("query", candidates, top_n=2)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_rerank_assigns_final_rank(reranker):
    candidates = [_fused("a"), _fused("b"), _fused("c")]

    result = await reranker.rerank("query", candidates, top_n=3)

    assert [r.final_rank for r in result] == [1, 2, 3]


@pytest.mark.asyncio
async def test_rerank_passes_query_document_pairs_to_cross_encoder(reranker, cross_encoder):
    candidates = [_fused("doc one"), _fused("doc two"), _fused("doc three")]

    await reranker.rerank("my query", candidates, top_n=3)

    cross_encoder.predict.assert_called_once_with(
        [("my query", "doc one"), ("my query", "doc two"), ("my query", "doc three")]
    )


@pytest.mark.asyncio
async def test_rerank_empty_candidates_returns_empty(reranker):
    result = await reranker.rerank("query", [], top_n=5)

    assert result == []


def test_name_returns_model_name(reranker):
    assert reranker.name == "BAAI/bge-reranker-large"
