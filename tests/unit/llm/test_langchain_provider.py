from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk

from src.llm.providers.langchain_provider import LangChainChatProvider, message_text


class _FakeChatModel:
    """Stands in for a BaseChatModel.

    Records what it was bound with, so the tests can assert that the port's
    per-call max_tokens/temperature actually reach the model -- they are
    per-instance settings on a real chat model, and binding is what bridges
    that difference.
    """

    def __init__(self, response: AIMessage | None = None, chunks: list[str] | None = None) -> None:
        self._response = response or AIMessage(content="")
        self._chunks = chunks or []
        self.bound_kwargs: dict[str, object] = {}
        self.invoked_with: list = []

    def bind(self, **kwargs: object) -> "_FakeChatModel":
        self.bound_kwargs = kwargs
        return self

    async def ainvoke(self, messages: list) -> AIMessage:
        self.invoked_with = messages
        return self._response

    async def astream(self, messages: list) -> AsyncIterator[AIMessageChunk]:
        self.invoked_with = messages
        for chunk in self._chunks:
            yield AIMessageChunk(content=chunk)


def _provider(model: _FakeChatModel) -> LangChainChatProvider:
    return LangChainChatProvider(model, model_id="test-model")  # type: ignore[arg-type]


class TestMessageText:
    def test_plain_string_content(self):
        assert message_text(AIMessage(content="ISMB 300")) == "ISMB 300"

    def test_content_blocks_are_flattened(self):
        message = AIMessage(
            content=[{"type": "text", "text": "ISMB "}, {"type": "text", "text": "300"}]
        )
        assert message_text(message) == "ISMB 300"

    def test_non_text_blocks_are_ignored(self):
        message = AIMessage(
            content=[
                {"type": "text", "text": "See drawing"},
                {"type": "image_url", "image_url": {"url": "http://example/x.png"}},
            ]
        )
        assert message_text(message) == "See drawing"


class TestComplete:
    async def test_returns_the_message_text(self):
        model = _FakeChatModel(response=AIMessage(content="Beam B-14 is an ISMB 300."))

        result = await _provider(model).complete("What is beam B-14?")

        assert result == "Beam B-14 is an ISMB 300."

    async def test_prompt_is_sent_as_a_human_message(self):
        model = _FakeChatModel(response=AIMessage(content="ok"))

        await _provider(model).complete("What is beam B-14?")

        assert [m.content for m in model.invoked_with] == ["What is beam B-14?"]

    async def test_per_call_generation_settings_are_bound(self):
        model = _FakeChatModel(response=AIMessage(content="ok"))

        await _provider(model).complete("q", max_tokens=64, temperature=0.0)

        assert model.bound_kwargs == {"max_tokens": 64, "temperature": 0.0}


class TestStream:
    async def test_yields_each_chunk_in_order(self):
        model = _FakeChatModel(chunks=["Beam ", "B-14 ", "is ISMB 300."])

        tokens = [t async for t in _provider(model).stream("q")]

        assert tokens == ["Beam ", "B-14 ", "is ISMB 300."]

    async def test_empty_chunks_are_not_yielded(self):
        """Providers emit metadata-only chunks with no text; forwarding them
        as empty tokens would show up as stray SSE events downstream."""
        model = _FakeChatModel(chunks=["Beam ", "", "B-14"])

        tokens = [t async for t in _provider(model).stream("q")]

        assert tokens == ["Beam ", "B-14"]


def test_model_id_is_reported_for_metrics_and_cost():
    assert _provider(_FakeChatModel()).model_id == "test-model"
