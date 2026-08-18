from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.providers.anthropic_provider import AnthropicProvider


class FakeBlock:
    def __init__(self, text, block_type="text"):
        self.text = text
        self.type = block_type


class FakeUsage:
    def __init__(self, input_tokens=12, output_tokens=34):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, blocks, usage=None):
        self.content = blocks
        self.usage = usage


class FakeStream:
    """Stands in for the async context manager `messages.stream` returns."""

    def __init__(self, tokens, final_usage=None):
        self._tokens = tokens
        self._final = FakeResponse([], usage=final_usage)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def _iter():
            for token in self._tokens:
                yield token

        return _iter()

    async def get_final_message(self):
        return self._final


@pytest.fixture
def provider():
    return AnthropicProvider(api_key="sk-ant-test", model="claude-haiku-4-5-20251001")


def test_model_id_property(provider):
    assert provider.model_id == "claude-haiku-4-5-20251001"


class TestComplete:
    async def test_joins_the_text_blocks(self, provider):
        provider._client.messages.create = AsyncMock(
            return_value=FakeResponse([FakeBlock("The refund window "), FakeBlock("is 30 days.")])
        )

        assert await provider.complete("policy?") == "The refund window is 30 days."

    async def test_ignores_non_text_blocks(self, provider):
        """Content is a list of typed blocks; only text blocks carry the answer,
        and a tool_use block joined in would corrupt it."""
        provider._client.messages.create = AsyncMock(
            return_value=FakeResponse(
                [FakeBlock("answer"), FakeBlock("{...}", block_type="tool_use")]
            )
        )

        assert await provider.complete("hello") == "answer"

    async def test_always_sends_max_tokens(self, provider):
        """Unlike the OpenAI shape, max_tokens is required by the Messages API
        rather than optional."""
        create = AsyncMock(return_value=FakeResponse([FakeBlock("ok")]))
        provider._client.messages.create = create

        await provider.complete("hello", max_tokens=256, temperature=0.7)

        create.assert_awaited_once_with(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.7,
            messages=[{"role": "user", "content": "hello"}],
        )


class TestStream:
    async def test_yields_each_text_delta(self, provider):
        provider._client.messages.stream = MagicMock(
            return_value=FakeStream(["Hello", " ", "world"])
        )

        assert [t async for t in provider.stream("hi")] == ["Hello", " ", "world"]

    async def test_records_usage_from_the_final_message(self, provider):
        """Usage arrives on dedicated stream events, not a final chunk, so it
        can only be read after the stream is drained."""
        provider._client.messages.stream = MagicMock(
            return_value=FakeStream(["x"], final_usage=FakeUsage(5, 7))
        )

        with patch("src.llm.providers.anthropic_provider.llm_tokens") as tokens:
            [t async for t in provider.stream("hi")]

        recorded = {call.kwargs["token_type"] for call in tokens.labels.call_args_list}
        assert recorded == {"prompt", "completion"}


class TestUsageRecording:
    def test_absent_usage_is_not_an_error(self, provider):
        with patch("src.llm.providers.anthropic_provider.llm_tokens") as tokens:
            provider._record_usage(None)

        tokens.labels.assert_not_called()

    def test_unparseable_counts_are_dropped_rather_than_raised(self, provider):
        """A metric is not worth failing a completed request over."""
        usage = MagicMock()
        usage.input_tokens = "not-a-number"

        with patch("src.llm.providers.anthropic_provider.llm_tokens") as tokens:
            provider._record_usage(usage)

        tokens.labels.assert_not_called()

    def test_zero_counts_record_nothing(self, provider):
        with patch("src.llm.providers.anthropic_provider.llm_tokens") as tokens:
            provider._record_usage(FakeUsage(0, 0))

        tokens.labels.assert_not_called()

    def test_both_directions_are_counted_separately(self, provider):
        with patch("src.llm.providers.anthropic_provider.llm_tokens") as tokens:
            provider._record_usage(FakeUsage(12, 34))

        by_type = {c.kwargs["token_type"]: c for c in tokens.labels.call_args_list}
        assert set(by_type) == {"prompt", "completion"}
        assert tokens.labels.return_value.inc.call_args_list[0].args == (12,)
        assert tokens.labels.return_value.inc.call_args_list[1].args == (34,)
