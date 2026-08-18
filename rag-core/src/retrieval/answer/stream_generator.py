from __future__ import annotations

from collections.abc import AsyncIterator

from src.llm.providers.base import LLMProvider
from src.monitoring.stage_tracer import traced_stage


class StreamGenerator:
    """Wraps LLMProvider.stream() in a traced_stage spanning the entire
    token stream -- the span opens before the first token and closes after
    the last, since `async with` inside an async generator function runs
    __aexit__ once the generator is fully consumed.
    """

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    @property
    def model_id(self) -> str:
        return self._llm.model_id

    async def generate(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        async with traced_stage(
            "answer_generation", model=self._llm.model_id, max_tokens=max_tokens
        ) as stage:
            token_count = 0
            async for token in self._llm.stream(
                prompt, max_tokens=max_tokens, temperature=temperature
            ):
                token_count += 1
                yield token
            stage.set_result(tokens_streamed=token_count)
