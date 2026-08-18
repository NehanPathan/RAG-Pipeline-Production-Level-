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

import base64
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage

from src.llm.providers.base import LLMProvider, VisionUnsupportedError

#: Model families known to accept image input. Matched as substrings of the
#: model id, because deployments name the same model a dozen ways
#: (`gpt-4o`, `gpt-4o-2024-08-06`, an Azure deployment alias). A model missing
#: from this list is treated as text-only, which costs a fallback rather than
#: a failed request mid-answer -- and an operator can override it explicitly.
_VISION_MODEL_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o3",
    "o4",
    "claude-3",
    "claude-4",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "gemini",
    "llava",
    "pixtral",
    "qwen2-vl",
    "qwen2.5-vl",
)


def _model_takes_images(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(hint in lowered for hint in _VISION_MODEL_HINTS)


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

    def __init__(
        self, model: BaseChatModel, model_id: str, supports_vision: bool | None = None
    ) -> None:
        self._model = model
        self._supports_vision = (
            _model_takes_images(model_id) if supports_vision is None else supports_vision
        )
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def _bound(self, max_tokens: int, temperature: float) -> Any:
        # max_tokens/temperature are per-call in this port but per-instance on
        # a chat model, so bind them for the duration of the call rather than
        # building a new client each time.
        return self._model.bind(max_tokens=max_tokens, temperature=temperature)

    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str:
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

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    async def describe_image(
        self,
        prompt: str,
        image_png: bytes,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """Send one image and one prompt as a multimodal message.

        Uses LangChain's typed content blocks, which every multimodal chat
        model in the registry accepts, so this stays one code path rather
        than one per vendor -- the same reasoning that made this adapter
        replace three hand-written providers.
        """
        if not self._supports_vision:
            raise VisionUnsupportedError(self.model_id)

        encoded = base64.b64encode(image_png).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
            ]
        )
        response = await self._bound(max_tokens, temperature).ainvoke([message])
        return message_text(response)
