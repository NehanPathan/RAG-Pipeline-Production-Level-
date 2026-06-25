import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.metadata_filter import MetadataFilterSpec
from src.domain.value_objects.processed_query import ProcessedQuery
from src.domain.value_objects.query_intent import IntentType, QueryIntent
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.domain.value_objects.retrieval_trace import RetrievalTrace
from src.retrieval.pipeline import QueryPipeline


def _processed_query(query="rewritten query"):
    return ProcessedQuery(
        original_query="original",
        rewritten_query=query,
        expanded_queries=[],
        intent=QueryIntent(type=IntentType.FACTUAL),
        selected_sources=[],
        filters=MetadataFilterSpec(),
    )


def _scored_chunk():
    chunk = DocumentChunk(document_id=uuid.uuid4(), content="v", position=0)
    return ScoredChunk(chunk=chunk, score=0.9, rank=1)


def _bm25_chunk():
    chunk = DocumentChunk(document_id=uuid.uuid4(), content="b", position=0)
    return BM25ScoredChunk(chunk=chunk, bm25_score=10.0, rank=1)


def _fused_chunk():
    chunk = DocumentChunk(document_id=uuid.uuid4(), content="f", position=0)
    return FusedChunk(chunk=chunk, rrf_score=0.01)


def _reranked_chunk():
    return RerankedChunk(fused=_fused_chunk(), rerank_score=0.9)


@pytest.fixture
def query_agent():
    agent = AsyncMock()
    agent.process = AsyncMock(return_value=_processed_query())
    return agent


@pytest.fixture
def hybrid_retriever():
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(
        return_value=([_scored_chunk()], [_bm25_chunk()], RetrievalTrace())
    )
    return retriever


@pytest.fixture
def fuser():
    f = AsyncMock()
    f.fuse = AsyncMock(return_value=[_fused_chunk()])
    return f


@pytest.fixture
def reranker():
    r = AsyncMock()
    r.rerank = AsyncMock(return_value=[_reranked_chunk()])
    return r


@pytest.fixture
def context_processor():
    cp = AsyncMock()
    chunk = CompressedChunk(reranked=_reranked_chunk(), compressed_content="c", token_count=5)
    cp.process = AsyncMock(return_value=([chunk], {}))
    return cp


@pytest.fixture
def answer_pipeline():
    ap = AsyncMock()

    async def _generate(query, chunks, citations, **kwargs):
        yield {"type": "token", "content": "Hello"}
        yield {"type": "done", "answer": "Hello", "citations": [], "model_used": "gpt-4o"}

    ap.generate = _generate
    return ap


@pytest.fixture
def semantic_cache():
    cache = AsyncMock()
    cache.lookup = AsyncMock(return_value=None)
    cache.store = AsyncMock()
    return cache


@pytest.fixture
def pipeline(
    query_agent, hybrid_retriever, fuser, reranker, context_processor, answer_pipeline, semantic_cache
):
    return QueryPipeline(
        query_agent=query_agent,
        hybrid_retriever=hybrid_retriever,
        fuser=fuser,
        reranker=reranker,
        context_processor=context_processor,
        answer_pipeline=answer_pipeline,
        semantic_cache=semantic_cache,
        vector_top_k=20,
        bm25_top_k=20,
        rerank_top_n=10,
    )


@pytest.mark.asyncio
async def test_inspect_runs_modules_a_through_d(pipeline, query_agent, hybrid_retriever, fuser, reranker):
    result = await pipeline.inspect("what is the refund policy?")

    query_agent.process.assert_called_once()
    hybrid_retriever.retrieve.assert_called_once()
    fuser.fuse.assert_called_once()
    reranker.rerank.assert_called_once()
    assert len(result.vector_results) == 1
    assert len(result.bm25_results) == 1
    assert len(result.fused_results) == 1
    assert len(result.reranked_results) == 1


@pytest.mark.asyncio
async def test_inspect_populates_trace_timings_and_counts(pipeline):
    result = await pipeline.inspect("query")

    assert result.trace.fused_count == 1
    assert result.trace.reranked_count == 1
    assert result.trace.query_processing_ms >= 0
    assert result.trace.fusion_ms >= 0
    assert result.trace.reranking_ms >= 0
    assert result.trace.total_ms >= 0


@pytest.mark.asyncio
async def test_inspect_does_not_invoke_context_processor(pipeline, context_processor):
    await pipeline.inspect("query")

    context_processor.process.assert_not_called()


@pytest.mark.asyncio
async def test_answer_returns_cached_result_on_hit(pipeline, semantic_cache, query_agent):
    semantic_cache.lookup = AsyncMock(
        return_value=SemanticCacheEntry(
            query_text="q", answer="cached answer", citations=[{"index": 1}]
        )
    )

    events = [event async for event in pipeline.answer("query")]

    assert len(events) == 1
    assert events[0]["answer"] == "cached answer"
    assert events[0]["cached"] is True
    query_agent.process.assert_not_called()


@pytest.mark.asyncio
async def test_answer_runs_full_pipeline_on_cache_miss(pipeline, query_agent, context_processor):
    events = [event async for event in pipeline.answer("query")]

    query_agent.process.assert_called_once()
    context_processor.process.assert_called_once()
    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(token_events) == 1
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_answer_stores_result_in_cache_after_done(pipeline, semantic_cache):
    _ = [event async for event in pipeline.answer("my query")]

    semantic_cache.store.assert_called_once()
    call = semantic_cache.store.call_args
    assert call.args[0] == "my query"
    assert call.args[1] == "Hello"
    assert call.kwargs["model_used"] == "gpt-4o"
