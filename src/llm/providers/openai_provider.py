from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        usage = response.usage.total_tokens if response.usage else None
        logger.debug("openai_completion", model=self._model, tokens=usage)
        return response.choices[0].message.content or ""

    async def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
