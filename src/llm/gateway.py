from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from src.governance import feature_flags
from src.llm.pricing import estimate_cost_usd
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import (
    gateway_cost_usd,
    gateway_fallbacks,
    gateway_requests,
    llm_tokens,
)

logger = get_logger(__name__)


@dataclass
class ProviderBinding:
    """One provider in a role's fallback chain."""

    name: str
    provider: LLMProvider

    @property
    def model_id(self) -> str:
        return self.provider.model_id


class AllProvidersFailedError(RuntimeError):
    """Every provider in the chain errored.

    Carries each failure rather than only the last: when a fallback chain
    fails, the *first* error is usually the real cause and the later ones are
    consequences (rate limits from the retry storm, say).
    """

    def __init__(self, role: str, failures: list[tuple[str, Exception]]) -> None:
        detail = "; ".join(f"{name}: {exc}" for name, exc in failures)
        super().__init__(f"All providers failed for role {role!r} — {detail}")
        self.role = role
        self.failures = failures


@dataclass
class LLMGateway(LLMProvider):
    """The single path every LLM call in the system takes.

    Implements `LLMProvider` so it is a drop-in replacement wherever a raw
    provider was passed before — every existing agent, compressor and
    generator gains fallback and cost tracking with no call-site change.

    Nothing else should construct a provider or call one directly. Routing
    every call through one object is what makes four otherwise-scattered
    concerns implementable at all:

      **Swappability** — changing provider is a config change
      (`LLM_FALLBACK_PROVIDERS=anthropic,openai`), not an edit to every
      call site. The old `llm/registry.py` returned a bare provider, so
      every caller was bound to whichever one it was handed.

      **Resilience** — a provider error falls through to the next in the
      chain instead of failing the request. A single vendor outage degrades
      quality rather than taking the product down.

      **Cost** — spend is priced and counted here, at the only place that
      sees both the model and the token counts. Cost measured anywhere else
      is measured in several places and agrees in none.

      **Governance** — the approved-model check has exactly one place to
      live, so an unapproved model cannot reach production through a code
      path that forgot to ask.
    """

    role: str
    chain: list[ProviderBinding]
    max_retries: int = 2
    _failures: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.chain:
            raise ValueError(f"LLMGateway for role {self.role!r} has an empty provider chain.")

    @property
    def primary(self) -> ProviderBinding:
        return self.chain[0]

    @property
    def model_id(self) -> str:
        """The model callers should report. Always the primary's — a
        fallback is an exception, and labelling every metric with whichever
        provider happened to answer would make the series unreadable."""
        return self.primary.model_id

    def _effective_chain(self) -> list[ProviderBinding]:
        """Honour `enable_llm_fallback`.

        Disabling fallback is a legitimate choice: silent failover hides a
        provider outage behind a quality change nobody can explain. With it
        off, the outage is loud.
        """
        if len(self.chain) == 1 or feature_flags.is_enabled("enable_llm_fallback"):
            return self.chain
        return self.chain[:1]

    async def complete(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> str:
        failures: list[tuple[str, Exception]] = []

        for index, binding in enumerate(self._effective_chain()):
            start = time.perf_counter()
            try:
                result = await binding.provider.complete(
                    prompt, max_tokens=max_tokens, temperature=temperature
                )
            except Exception as exc:
                failures.append((binding.name, exc))
                gateway_requests.labels(
                    provider=binding.name, role=self.role, outcome="error"
                ).inc()
                logger.warning(
                    "gateway_provider_failed",
                    role=self.role,
                    provider=binding.name,
                    attempt=index + 1,
                    error=str(exc),
                )
                self._record_fallback(index)
                continue

            gateway_requests.labels(provider=binding.name, role=self.role, outcome="ok").inc()
            self._price(binding, prompt, result)
            logger.debug(
                "gateway_completion",
                role=self.role,
                provider=binding.name,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return result

        raise AllProvidersFailedError(self.role, failures)

    async def stream(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3
    ) -> AsyncIterator[str]:
        """Stream tokens, failing over only before the first token.

        Once a token has reached the client, switching providers would splice
        two different answers together — so a mid-stream failure propagates
        rather than silently producing incoherent output. The whole point of
        the guardrails downstream is that the answer is one coherent thing.
        """
        failures: list[tuple[str, Exception]] = []

        for index, binding in enumerate(self._effective_chain()):
            started = False
            try:
                async for token in binding.provider.stream(
                    prompt, max_tokens=max_tokens, temperature=temperature
                ):
                    started = True
                    yield token
            except Exception as exc:
                if started:
                    gateway_requests.labels(
                        provider=binding.name, role=self.role, outcome="error_midstream"
                    ).inc()
                    logger.error(
                        "gateway_stream_failed_midstream",
                        role=self.role,
                        provider=binding.name,
                        error=str(exc),
                    )
                    raise
                failures.append((binding.name, exc))
                gateway_requests.labels(
                    provider=binding.name, role=self.role, outcome="error"
                ).inc()
                logger.warning(
                    "gateway_stream_failed",
                    role=self.role,
                    provider=binding.name,
                    error=str(exc),
                )
                self._record_fallback(index)
                continue

            gateway_requests.labels(provider=binding.name, role=self.role, outcome="ok").inc()
            return

        raise AllProvidersFailedError(self.role, failures)

    def _record_fallback(self, failed_index: int) -> None:
        chain = self._effective_chain()
        next_index = failed_index + 1
        if next_index < len(chain):
            gateway_fallbacks.labels(
                from_provider=chain[failed_index].name,
                to_provider=chain[next_index].name,
            ).inc()

    def _price(self, binding: ProviderBinding, prompt: str, completion: str) -> None:
        """Estimate and record spend.

        Uses a 4-chars-per-token approximation. The provider reports exact
        counts for its own metrics (see OpenAIProvider._record_usage); this
        is the provider-agnostic estimate that makes cost comparable *across*
        providers, which is the number a fallback chain makes you care about.
        """
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(completion) // 4)
        model = binding.model_id

        llm_tokens.labels(model=model, token_type="prompt_estimated").inc(prompt_tokens)
        llm_tokens.labels(model=model, token_type="completion_estimated").inc(completion_tokens)

        cost = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        if cost is None:
            # Unpriced model: log once per call rather than recording zero,
            # which would understate spend on exactly the models nobody has
            # reviewed the price of yet.
            logger.debug("gateway_model_unpriced", model=model)
            return
        gateway_cost_usd.labels(provider=binding.name, model=model).inc(cost)

    def describe(self) -> dict:
        return {
            "role": self.role,
            "chain": [{"provider": b.name, "model": b.model_id} for b in self.chain],
            "fallback_enabled": feature_flags.is_enabled("enable_llm_fallback"),
            "max_retries": self.max_retries,
        }
