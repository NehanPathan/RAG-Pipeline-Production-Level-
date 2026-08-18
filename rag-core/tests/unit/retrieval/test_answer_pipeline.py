import uuid
from unittest.mock import MagicMock

import pytest

from src.domain.entities.conversation import Citation
from src.domain.value_objects.context_bundle import AssembledContext
from src.retrieval.answer.answer_pipeline import AnswerPipeline


@pytest.fixture
def context_assembler():
    assembler = MagicMock()
    assembler.assemble = MagicMock(
        return_value=AssembledContext(formatted_text="[1] context", total_tokens=10)
    )
    return assembler


@pytest.fixture
def prompt_builder():
    builder = MagicMock()
    builder.build = MagicMock(return_value="built prompt")
    return builder


@pytest.fixture
def stream_generator():
    async def _generate(prompt, max_tokens, temperature):
        for token in ["The ", "answer ", "is X [1]."]:
            yield token

    generator = MagicMock()
    generator.generate = _generate
    return generator


@pytest.fixture
def citation():
    return Citation(index=1, source_name="doc.pdf", document_name="doc.pdf", chunk_id=uuid.uuid4())


@pytest.fixture
def citation_validator(citation):
    validator = MagicMock()
    validator.validate = MagicMock(return_value=[citation])
    return validator


@pytest.fixture
def pipeline(context_assembler, prompt_builder, stream_generator, citation_validator):
    return AnswerPipeline(
        context_assembler=context_assembler,
        prompt_builder=prompt_builder,
        stream_generator=stream_generator,
        citation_validator=citation_validator,
    )


@pytest.mark.asyncio
async def test_generate_yields_token_events_then_done(pipeline, citation):
    events = [event async for event in pipeline.generate("query", [], {})]

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert [e["content"] for e in token_events] == ["The ", "answer ", "is X [1]."]
    assert len(done_events) == 1
    assert done_events[0]["answer"] == "The answer is X [1]."
    assert done_events[0]["citations"][0]["index"] == 1
    assert done_events[0]["citations"][0]["chunk_id"] == str(citation.chunk_id)


@pytest.mark.asyncio
async def test_generate_yields_error_event_on_stream_failure(context_assembler, prompt_builder, citation_validator):
    async def _failing_generate(prompt, max_tokens, temperature):
        raise RuntimeError("LLM down")
        yield  # pragma: no cover

    failing_generator = MagicMock()
    failing_generator.generate = _failing_generate

    pipeline = AnswerPipeline(
        context_assembler=context_assembler,
        prompt_builder=prompt_builder,
        stream_generator=failing_generator,
        citation_validator=citation_validator,
    )

    events = [event async for event in pipeline.generate("query", [], {})]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "generation_failed"
    assert "LLM down" in events[0]["message"]


@pytest.mark.asyncio
async def test_generate_builds_prompt_from_assembled_context(pipeline, context_assembler, prompt_builder):
    _ = [event async for event in pipeline.generate("my query", [], {})]

    context_assembler.assemble.assert_called_once()
    prompt_builder.build.assert_called_once_with("my query", context_assembler.assemble.return_value)


@pytest.mark.asyncio
async def test_generate_passes_max_tokens_and_temperature_through(pipeline):
    events = [
        event async for event in pipeline.generate("q", [], {}, max_tokens=512, temperature=0.1)
    ]

    assert any(e["type"] == "done" for e in events)
