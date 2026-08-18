import pytest

from src.monitoring.langfuse_tracer import get_langfuse_client, reset_langfuse_client
from src.monitoring.stage_tracer import traced_stage
from src.monitoring.tracing import get_tracer, reset_tracing


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_langfuse_client()
    reset_tracing()
    yield
    reset_langfuse_client()
    reset_tracing()


@pytest.mark.asyncio
async def test_traced_stage_records_result_and_duration():
    async with traced_stage("vector_search", query="refund policy", top_k=20) as stage:
        stage.set_result(chunks_found=5)

    assert stage._result["chunks_found"] == 5
    assert "duration_ms" in stage._result
    assert stage._result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_traced_stage_drops_none_attributes():
    async with traced_stage("bm25_search", query="x", filters=None) as stage:
        pass

    assert "filters" not in stage._attributes
    assert stage._attributes["query"] == "x"


@pytest.mark.asyncio
async def test_traced_stage_propagates_exception():
    with pytest.raises(RuntimeError, match="search backend down"):
        async with traced_stage("vector_search", query="x"):
            raise RuntimeError("search backend down")


@pytest.mark.asyncio
async def test_traced_stage_records_duration_even_on_failure():
    stage_ref = None
    try:
        async with traced_stage("vector_search", query="x") as stage:
            stage_ref = stage
            raise ValueError("boom")
    except ValueError:
        pass

    assert "duration_ms" in stage_ref._result


@pytest.mark.asyncio
async def test_traced_stage_works_without_langfuse_keys_configured():
    # Default test settings have no langfuse keys -- this must not raise.
    async with traced_stage("rerank", query="x", candidates=10) as stage:
        stage.set_result(reranked=10)


def test_get_tracer_usable_without_configure_tracing():
    tracer = get_tracer()
    with tracer.start_as_current_span("manual_span") as span:
        span.set_attribute("ok", True)


def test_get_langfuse_client_is_singleton():
    client_a = get_langfuse_client()
    client_b = get_langfuse_client()
    assert client_a is client_b
