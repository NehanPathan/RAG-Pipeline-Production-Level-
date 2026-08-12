from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import llm_tokens

logger = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    """Claude via the Anthropic Messages API.

    Two differences from the OpenAI shape are worth knowing:
    `max_tokens` is required rather than optional, and usage arrives on
    dedicated stream events instead of a final chunk.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(getattr(response, "usage", None))
        # Content is a list of typed blocks; only text blocks carry the answer.
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

    async def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            self._record_usage(getattr(final, "usage", None))

    def _record_usage(self, usage: object | None) -> None:
        if usage is None:
            return
        try:
            prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        except (TypeError, ValueError):
            return
        if prompt_tokens:
            llm_tokens.labels(model=self._model, token_type="prompt").inc(prompt_tokens)
        if completion_tokens:
            llm_tokens.labels(model=self._model, token_type="completion").inc(completion_tokens)
