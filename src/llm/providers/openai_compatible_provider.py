from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import llm_tokens

logger = get_logger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """Any vendor exposing an OpenAI-shaped `/chat/completions` endpoint.

    One class covers Azure OpenAI, OpenRouter, Ollama (which serves an
    OpenAI-compatible API at `/v1`), vLLM, Groq, Together and most local
    inference servers — they differ only in base URL, key and model name.
    Writing a separate provider per vendor would be five copies of this file
    with the constants changed.

    `stream_options` is negotiated rather than assumed: the parameter is an
    OpenAI extension and several compatible servers reject an unknown field
    outright, so a first failure disables it for this instance and retries.
    Without that, enabling token accounting would break every non-OpenAI
    vendor in the chain.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        vendor: str = "openai_compatible",
        supports_usage_in_stream: bool = True,
        timeout: float = 60.0,
    ) -> None:
        # Some local servers need no key but the SDK requires a non-empty one.
        self._client = AsyncOpenAI(
            api_key=api_key or "not-required", base_url=base_url, timeout=timeout
        )
        self._model = model
        self._vendor = vendor
        self._stream_usage = supports_usage_in_stream

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def vendor(self) -> str:
        return self._vendor

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._record_usage(getattr(response, "usage", None))
        return response.choices[0].message.content or ""

    async def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        kwargs: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if self._stream_usage:
            kwargs["stream_options"] = {"include_usage": True}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not self._stream_usage or not _looks_like_unsupported_param(exc):
                raise
            # Remember for subsequent calls so the cost is paid once, not on
            # every request to this vendor.
            logger.info(
                "stream_options_unsupported_disabling", vendor=self._vendor, model=self._model
            )
            self._stream_usage = False
            kwargs.pop("stream_options", None)
            response = await self._client.chat.completions.create(**kwargs)

        async for chunk in response:
            if getattr(chunk, "usage", None):
                self._record_usage(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def _record_usage(self, usage: object | None) -> None:
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


def _looks_like_unsupported_param(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("stream_options", "unknown parameter", "unrecognized", "unsupported")
    )
