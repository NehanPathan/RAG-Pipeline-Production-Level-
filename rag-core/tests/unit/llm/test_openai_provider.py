from unittest.mock import AsyncMock

import pytest

from src.llm.providers.openai_provider import OpenAIProvider


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class FakeCompletion:
    def __init__(self, content, total_tokens=10):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage(total_tokens)


class FakeDelta:
    def __init__(self, content):
        self.content = content


class FakeChunkChoice:
    def __init__(self, content):
        self.delta = FakeDelta(content)


class FakeChunk:
    def __init__(self, content):
        self.choices = [FakeChunkChoice(content)]


async def _fake_stream(tokens):
    for token in tokens:
        yield FakeChunk(token)


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_complete_returns_message_content(provider):
    provider._client.chat.completions.create = AsyncMock(
        return_value=FakeCompletion("The refund window is 30 days.")
    )

    result = await provider.complete("What is the refund policy?", max_tokens=100, temperature=0.1)

    assert result == "The refund window is 30 days."


@pytest.mark.asyncio
async def test_complete_returns_empty_string_when_content_is_none(provider):
    provider._client.chat.completions.create = AsyncMock(return_value=FakeCompletion(None))

    result = await provider.complete("hello")

    assert result == ""


@pytest.mark.asyncio
async def test_complete_passes_model_and_params(provider):
    mock_create = AsyncMock(return_value=FakeCompletion("ok"))
    provider._client.chat.completions.create = mock_create

    await provider.complete("hello", max_tokens=256, temperature=0.7)

    mock_create.assert_called_once_with(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=256,
        temperature=0.7,
    )


@pytest.mark.asyncio
async def test_stream_yields_only_non_empty_deltas(provider):
    provider._client.chat.completions.create = AsyncMock(
        return_value=_fake_stream(["Hello", None, " world", ""])
    )

    tokens = [token async for token in provider.stream("hello")]

    assert tokens == ["Hello", " world"]


def test_model_id_property(provider):
    assert provider.model_id == "gpt-4o-mini"
