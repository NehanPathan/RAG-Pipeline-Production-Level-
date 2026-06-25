import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk
from src.retrieval.rerankers.cohere_reranker import CohereReranker


def _fused(content, chunk_id=None):
    return FusedChunk(
        chunk=DocumentChunk(
            id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
        ),
        rrf_score=0.01,
    )


@dataclass
class FakeResultItem:
    index: int
    relevance_score: float


@dataclass
class FakeRerankResponse:
    results: list


@pytest.fixture
def client():
    return AsyncMock()


@pytest.fixture
def reranker(client):
    return CohereReranker(client=client, model="rerank-english-v3.0")


@pytest.mark.asyncio
async def test_rerank_maps_results_back_to_fused_chunks_by_index(reranker, client):
    candidates = [_fused("a"), _fused("b"), _fused("c")]
    client.rerank = AsyncMock(
        return_value=FakeRerankResponse(
            results=[
                FakeResultItem(index=1, relevance_score=0.95),
                FakeResultItem(index=0, relevance_score=0.4),
            ]
        )
    )

    result = await reranker.rerank("query", candidates, top_n=2)

    assert len(result) == 2
    assert result[0].fused.chunk.content == "b"
    assert result[0].rerank_score == 0.95
    assert result[0].final_rank == 1
    assert result[1].fused.chunk.content == "a"
    assert result[1].final_rank == 2


@pytest.mark.asyncio
async def test_rerank_passes_model_query_documents_and_top_n(reranker, client):
    candidates = [_fused("doc one"), _fused("doc two")]
    client.rerank = AsyncMock(return_value=FakeRerankResponse(results=[]))

    await reranker.rerank("my query", candidates, top_n=5)

    client.rerank.assert_called_once_with(
        model="rerank-english-v3.0", query="my query", documents=["doc one", "doc two"], top_n=5
    )


@pytest.mark.asyncio
async def test_rerank_empty_candidates_returns_empty_without_calling_api(reranker, client):
    result = await reranker.rerank("query", [], top_n=5)

    assert result == []
    client.rerank.assert_not_called()


def test_name_returns_model(reranker):
    assert reranker.name == "rerank-english-v3.0"
