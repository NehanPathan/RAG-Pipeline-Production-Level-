from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMProvider(ABC):
    """Abstract base for chat-completion LLM providers.

    `complete()`'s single-prompt signature matches what
    `ingestion.enrichers.llm_enricher.LLMMetadataEnricher` already assumes —
    that code predates this ABC and is left unchanged. `stream()` is new,
    needed by the Answer Pipeline (Module G) for token-by-token generation.

    `stream()` is declared as a plain `def` returning `AsyncIterator[str]`
    (not `async def`) so concrete implementations are async generator
    functions (`async def stream(...): yield ...`) and callers can do
    `async for token in provider.stream(...)` directly, with no `await`
    on the call itself.
    """

    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str: ...

    @abstractmethod
    def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...
