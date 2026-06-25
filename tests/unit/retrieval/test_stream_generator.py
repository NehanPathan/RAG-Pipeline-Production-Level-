import pytest

from src.retrieval.answer.stream_generator import StreamGenerator


@pytest.fixture
def generator(mock_llm_provider):
    return StreamGenerator(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_generate_yields_tokens_from_provider(generator, mock_llm_provider):
    async def _stream(prompt, max_tokens, temperature):
        for token in ["Hello", " ", "world"]:
            yield token

    mock_llm_provider.stream = _stream

    tokens = [t async for t in generator.generate("prompt")]

    assert tokens == ["Hello", " ", "world"]


@pytest.mark.asyncio
async def test_generate_passes_through_max_tokens_and_temperature(generator, mock_llm_provider):
    received = {}

    async def _stream(prompt, max_tokens, temperature):
        received["prompt"] = prompt
        received["max_tokens"] = max_tokens
        received["temperature"] = temperature
        yield "ok"

    mock_llm_provider.stream = _stream

    _ = [t async for t in generator.generate("my prompt", max_tokens=256, temperature=0.7)]

    assert received == {"prompt": "my prompt", "max_tokens": 256, "temperature": 0.7}


@pytest.mark.asyncio
async def test_generate_empty_stream_yields_nothing(generator, mock_llm_provider):
    async def _empty_stream(prompt, max_tokens, temperature):
        if False:
            yield  # pragma: no cover

    mock_llm_provider.stream = _empty_stream

    tokens = [t async for t in generator.generate("prompt")]

    assert tokens == []
