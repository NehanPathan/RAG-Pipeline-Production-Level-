from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import llm_tokens

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
        self._record_usage(response.usage)
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
            # Without this the API reports no usage at all for streamed
            # calls, which is why `rag_llm_tokens_total` previously stayed at
            # zero for the one code path that spends the most tokens. The
            # usage arrives in a final chunk that carries no choices.
            stream_options={"include_usage": True},
        )
        async for chunk in response:
            if getattr(chunk, "usage", None):
                self._record_usage(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def _record_usage(self, usage: object | None) -> None:
        """Publish token counts to Prometheus.

        Cost is the metric a governance review asks for first and the one
        that is hardest to reconstruct after the fact, so it is recorded at
        the only place that sees the authoritative numbers: the provider.
        Wrapped defensively because usage is optional in the API schema and a
        missing field must not break generation.
        """
        if usage is None:
            return
        try:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        except (TypeError, ValueError):
            return
        if prompt_tokens:
            llm_tokens.labels(model=self._model, token_type="prompt").inc(prompt_tokens)
        if completion_tokens:
            llm_tokens.labels(model=self._model, token_type="completion").inc(completion_tokens)
