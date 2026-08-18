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
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.rbac import Principal, Role
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


def _chunk(content: str, sensitivity: Sensitivity = Sensitivity.INTERNAL) -> DocumentChunk:
    chunk = DocumentChunk(document_id=uuid.uuid4(), content=content, position=0)
    chunk.sensitivity = sensitivity
    return chunk


def _scored_chunk(sensitivity: Sensitivity = Sensitivity.INTERNAL):
    return ScoredChunk(chunk=_chunk("v", sensitivity), score=0.9, rank=1)


def _bm25_chunk(sensitivity: Sensitivity = Sensitivity.INTERNAL):
    return BM25ScoredChunk(chunk=_chunk("b", sensitivity), bm25_score=10.0, rank=1)


def _fused_chunk():
    return FusedChunk(chunk=_chunk("f"), rrf_score=0.01)


def _reranked_chunk():
    return RerankedChunk(fused=_fused_chunk(), rerank_score=0.9)


@pytest.fixture
def cleared_principal() -> Principal:
    """An identity cleared for the INTERNAL test fixtures.

    Every pipeline test needs one: `inspect()`/`answer()` fall back to an
    anonymous principal with `public` clearance, which correctly filters out
    the internal-by-default chunks these fixtures produce.
    """
    return Principal(user_id=uuid.uuid4(), role=Role.ADMIN.value, clearance=Sensitivity.RESTRICTED)


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
    ap.model_id = "gpt-4o"

    async def _generate(query, chunks, citations, **kwargs):
        yield {"type": "token", "content": "Hello"}
        # A citation is required for the answer to clear the grounding policy
        # (C-GOV-04); an uncited answer is refused, which is its own test below.
        yield {
            "type": "done",
            "answer": "Hello [1]",
            "citations": [{"index": 1, "chunk_id": str(uuid.uuid4())}],
            "model_used": "gpt-4o",
        }

    ap.generate = _generate
    return ap


@pytest.fixture
def semantic_cache():
    cache = AsyncMock()
    cache.lookup = AsyncMock(return_value=None)
    cache.store = AsyncMock()
    cache.invalidate_document = AsyncMock()
    return cache


@pytest.fixture
def pipeline(
    query_agent,
    hybrid_retriever,
    fuser,
    reranker,
    context_processor,
    answer_pipeline,
    semantic_cache,
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
async def test_inspect_runs_modules_a_through_d(
    pipeline, query_agent, hybrid_retriever, fuser, reranker, cleared_principal
):
    result = await pipeline.inspect("what is the refund policy?", principal=cleared_principal)

    query_agent.process.assert_called_once()
    hybrid_retriever.retrieve.assert_called_once()
    fuser.fuse.assert_called_once()
    reranker.rerank.assert_called_once()
    assert len(result.vector_results) == 1
    assert len(result.bm25_results) == 1
    assert len(result.fused_results) == 1
    assert len(result.reranked_results) == 1


@pytest.mark.asyncio
async def test_inspect_populates_trace_timings_and_counts(pipeline, cleared_principal):
    result = await pipeline.inspect("query", principal=cleared_principal)

    assert result.trace.fused_count == 1
    assert result.trace.reranked_count == 1
    assert result.trace.query_processing_ms >= 0
    assert result.trace.fusion_ms >= 0
    assert result.trace.reranking_ms >= 0
    assert result.trace.total_ms >= 0


@pytest.mark.asyncio
async def test_inspect_does_not_invoke_context_processor(
    pipeline, context_processor, cleared_principal
):
    await pipeline.inspect("query", principal=cleared_principal)

    context_processor.process.assert_not_called()


@pytest.mark.asyncio
async def test_answer_returns_cached_result_on_hit(
    pipeline, semantic_cache, query_agent, cleared_principal
):
    semantic_cache.lookup = AsyncMock(
        return_value=SemanticCacheEntry(
            query_text="q", answer="cached answer", citations=[{"index": 1}]
        )
    )

    events = [e async for e in pipeline.answer("query", principal=cleared_principal)]

    assert len(events) == 1
    assert events[0]["answer"] == "cached answer"
    assert events[0]["cached"] is True
    query_agent.process.assert_not_called()


@pytest.mark.asyncio
async def test_answer_runs_full_pipeline_on_cache_miss(
    pipeline, query_agent, context_processor, cleared_principal
):
    events = [e async for e in pipeline.answer("query", principal=cleared_principal)]

    query_agent.process.assert_called_once()
    context_processor.process.assert_called_once()
    assert len([e for e in events if e["type"] == "token"]) == 1
    assert len([e for e in events if e["type"] == "done"]) == 1


@pytest.mark.asyncio
async def test_answer_stores_result_in_cache_after_done(
    pipeline, semantic_cache, cleared_principal
):
    _ = [e async for e in pipeline.answer("my query", principal=cleared_principal)]

    semantic_cache.store.assert_called_once()
    call = semantic_cache.store.call_args
    # The resolved query, not the raw one -- see
    # test_a_follow_up_is_not_cached_under_its_own_words.
    assert call.args[0] == "rewritten query"
    assert call.args[1] == "Hello [1]"
    assert call.kwargs["model_used"] == "gpt-4o"


@pytest.mark.asyncio
async def test_done_event_carries_trace_id_and_refusal_flag(pipeline, cleared_principal):
    events = [e async for e in pipeline.answer("query", principal=cleared_principal)]
    done = next(e for e in events if e["type"] == "done")

    assert "trace_id" in done
    assert done["refused"] is False


class TestGovernanceControls:
    """The controls the pipeline applies, each asserted directly."""

    @pytest.mark.asyncio
    async def test_clearance_filters_internal_content_from_public_caller(self, pipeline):
        """No principal means an anonymous, public-clearance caller. The
        INTERNAL fixtures must not reach them."""
        result = await pipeline.inspect("query")

        assert result.vector_results == []
        assert result.bm25_results == []
        assert result.blocked_by_clearance == 2

    @pytest.mark.asyncio
    async def test_public_content_reaches_a_public_caller(self, hybrid_retriever, pipeline):
        hybrid_retriever.retrieve = AsyncMock(
            return_value=(
                [_scored_chunk(Sensitivity.PUBLIC)],
                [_bm25_chunk(Sensitivity.PUBLIC)],
                RetrievalTrace(),
            )
        )

        result = await pipeline.inspect("query")

        assert len(result.vector_results) == 1
        assert result.blocked_by_clearance == 0

    @pytest.mark.asyncio
    async def test_clearance_allow_list_is_pushed_into_the_search_filters(
        self, pipeline, hybrid_retriever, cleared_principal
    ):
        """The pre-filter is the primary control; the post-filter only backs
        it up. Assert the allow-list actually reaches the backends."""
        await pipeline.inspect("query", principal=cleared_principal)

        kwargs = hybrid_retriever.retrieve.call_args.kwargs
        assert kwargs["vector_filter"].sensitivity_in == [
            "public",
            "internal",
            "confidential",
            "restricted",
        ]
        assert kwargs["bm25_filter"].sensitivity_in == kwargs["vector_filter"].sensitivity_in

    @pytest.mark.asyncio
    async def test_empty_context_is_refused_before_generation(
        self, pipeline, context_processor, answer_pipeline, cleared_principal
    ):
        context_processor.process = AsyncMock(return_value=([], {}))
        generated = False

        async def _generate(*args, **kwargs):
            nonlocal generated
            generated = True
            yield {"type": "done", "answer": "made up", "citations": []}

        answer_pipeline.generate = _generate

        events = [e async for e in pipeline.answer("query", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")

        assert done["refused"] is True
        assert done["refusal_reason"] == "C-GOV-03"
        assert generated is False, "refusal must happen before the model is called"

    @pytest.mark.asyncio
    async def test_uncited_answer_is_refused(self, pipeline, answer_pipeline, cleared_principal):
        async def _generate(*args, **kwargs):
            yield {"type": "done", "answer": "confident but ungrounded", "citations": []}

        answer_pipeline.generate = _generate

        events = [e async for e in pipeline.answer("query", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")

        assert done["refused"] is True
        assert done["refusal_reason"] == "C-GOV-04"
        assert "confident but ungrounded" not in done["answer"]

    @pytest.mark.asyncio
    async def test_refused_answer_is_not_cached(
        self, pipeline, answer_pipeline, semantic_cache, cleared_principal
    ):
        """Caching a refusal would serve it to every similar future query."""

        async def _generate(*args, **kwargs):
            yield {"type": "done", "answer": "ungrounded", "citations": []}

        answer_pipeline.generate = _generate

        _ = [e async for e in pipeline.answer("query", principal=cleared_principal)]

        semantic_cache.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_describe_config_reports_the_live_configuration(self, pipeline):
        """Recorded against every evaluation run; a hardcoded value here makes
        cross-run comparison unsound."""
        config = pipeline.describe_config()

        assert config["vector_top_k"] == 20
        assert config["bm25_top_k"] == 20
        assert config["rerank_top_n"] == 10
        assert config["generator_model"] == "gpt-4o"
        assert "policy_version" in config

    @pytest.mark.asyncio
    async def test_invalidate_cached_document_reaches_the_cache(self, pipeline, semantic_cache):
        document_id = uuid.uuid4()

        await pipeline.invalidate_cached_document(document_id)

        semantic_cache.invalidate_document.assert_called_once_with(document_id)


class TestDoneEventStamping:
    """Every `done` event must carry the redacted question, whichever of the
    five paths produced it. A refusal that omitted it made the raw question
    fall through to persistence -- and refusals are common on a sparse corpus."""

    @pytest.mark.asyncio
    async def test_rag_answer_carries_the_question(self, pipeline, cleared_principal):
        events = [
            e async for e in pipeline.answer("what is the policy", principal=cleared_principal)
        ]
        done = next(e for e in events if e["type"] == "done")
        assert done["question"] == "what is the policy"

    @pytest.mark.asyncio
    async def test_refusal_carries_the_question(
        self, pipeline, context_processor, cleared_principal
    ):
        """The path that previously leaked: no context -> refusal -> the raw
        question was persisted because the event had no `question` key."""
        context_processor.process = AsyncMock(return_value=([], {}))

        events = [e async for e in pipeline.answer("anything", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")

        assert done["refused"] is True
        assert done["question"] == "anything"
        assert done["grounded"] is False

    @pytest.mark.asyncio
    async def test_greeting_route_carries_the_question(self, pipeline, cleared_principal):
        events = [e async for e in pipeline.answer("hi", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")
        assert done["question"] == "hi"

    @pytest.mark.asyncio
    async def test_cache_hit_carries_the_question(
        self, pipeline, semantic_cache, cleared_principal
    ):
        semantic_cache.lookup = AsyncMock(
            return_value=SemanticCacheEntry(query_text="q", answer="cached", citations=[])
        )
        events = [e async for e in pipeline.answer("cached one", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")
        assert done["question"] == "cached one"

    @pytest.mark.asyncio
    async def test_every_done_event_has_the_full_shape(self, pipeline, cleared_principal):
        """Clients branch on these keys; a missing one is a client-side
        KeyError rather than a graceful degradation."""
        events = [e async for e in pipeline.answer("hi", principal=cleared_principal)]
        done = next(e for e in events if e["type"] == "done")
        for key in ("question", "route", "route_source", "grounded", "answer", "citations"):
            assert key in done, f"done event is missing {key!r}"


@pytest.mark.asyncio
async def test_a_follow_up_is_not_cached_under_its_own_words(
    pipeline, semantic_cache, cleared_principal
):
    """The semantic cache has no notion of a conversation.

    Keyed on query similarity alone, a follow-up cached under its own words
    is served to every other conversation that phrases one the same way:
    "What about the bolts?" asked about one drawing came back verbatim, cited
    and confident, for a different drawing in a different conversation.

    Storing the resolved form fixes it without a second cache key. An
    elliptical question resolves to something naming its own subject, which
    no other conversation's raw text matches; a self-contained question
    resolves to roughly itself and still caches normally.
    """
    _ = [
        e
        async for e in pipeline.answer(
            "what about the bolts?",
            principal=cleared_principal,
            history=[("user", "tell me about SSD09.0-02"), ("assistant", "a beam connection")],
        )
    ]

    stored_key = semantic_cache.store.call_args.args[0]
    assert stored_key != "what about the bolts?", (
        "an elliptical follow-up cached under its raw text leaks across conversations"
    )
