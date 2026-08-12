"""Adapter from any LangChain chat model to this codebase's LLMProvider port.

Replaces three hand-written provider implementations (OpenAI, Anthropic, and
a shared OpenAI-compatible client used for Ollama/OpenRouter/Azure). Those
were maintaining retry behaviour, streaming-chunk parsing, usage extraction
and per-vendor request quirks by hand -- all of which the provider packages
already do, and keep doing as the vendor APIs move.

What is deliberately *not* delegated is everything the LLMGateway wraps
around this: the approved-provider policy check, per-provider Prometheus
metrics, cost accounting, the feature-flag-gated fallback chain, and the
rule that a stream never fails over once a token has reached the client.
LangChain's `.with_fallbacks()` covers only the failover, and silently --
which is the opposite of what a governance layer needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from src.llm.providers.base import LLMProvider


def message_text(message: BaseMessage) -> str:
    """Flatten a message's content to plain text.

    `BaseMessage.text` is a property in langchain-core 1.x and was a method
    before that; the value it returns is currently a str subclass that is
    also callable, to keep old call sites working. Testing for `str` first is
    therefore what avoids the deprecation path -- calling it still works but
    is the deprecated spelling. Content itself may be a plain string or a
    list of typed blocks, and handling both here keeps that shape mismatch
    out of every call site.
    """
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return str(text)
    if callable(text):
        return str(text())

    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return str(content)


class LangChainChatProvider(LLMProvider):
    """Wraps a `BaseChatModel` in the single-prompt interface this app uses.

    The app's own port is intentionally narrow -- one prompt string in, text
    out -- because that is what every agent, compressor, classifier and
    enricher here actually needs. Keeping the port narrow is what let the
    provider implementation underneath be swapped without touching a single
    caller.
    """

    def __init__(self, model: BaseChatModel, model_id: str) -> None:
        self._model = model
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def _bound(self, max_tokens: int, temperature: float) -> Any:
        # max_tokens/temperature are per-call in this port but per-instance on
        # a chat model, so bind them for the duration of the call rather than
        # building a new client each time.
        return self._model.bind(max_tokens=max_tokens, temperature=temperature)

    async def complete(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> str:
        response = await self._bound(max_tokens, temperature).ainvoke(
            [HumanMessage(content=prompt)]
        )
        return message_text(response)

    async def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        async for chunk in self._bound(max_tokens, temperature).astream(
            [HumanMessage(content=prompt)]
        ):
            token = message_text(chunk)
            if token:
                yield token
