from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class VisionUnsupportedError(RuntimeError):
    """Raised when a provider is asked for vision it does not have.

    Carries the model id because the operator's next question is always
    "which one", and the answer decides whether to change a setting or a
    deployment.
    """

    def __init__(self, model_id: str) -> None:
        super().__init__(f"Provider {model_id!r} does not support image input.")
        self.model_id = model_id


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
    async def complete(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> str: ...

    @abstractmethod
    def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @property
    def supports_vision(self) -> bool:
        """Whether this provider accepts image input.

        Declared here rather than in a separate vision port because a vision
        model *is* an LLM provider -- giving it its own abstraction would mean
        a second gateway, a second policy check and a second set of metrics,
        all of which the governance layer would then have to be taught about
        twice. Defaults to False so a provider that has not opted in is
        treated as text-only rather than tried and failed at request time.
        """
        return False

    async def describe_image(
        self,
        prompt: str,
        image_png: bytes,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """Answer `prompt` about `image_png`.

        Raises rather than degrading to a text-only call: an image question
        answered without the image is a fabrication, and one that would be
        indistinguishable from a real answer.
        """
        raise VisionUnsupportedError(self.model_id)
