from __future__ import annotations

import pytest

from src.governance import feature_flags
from src.llm.gateway import AllProvidersFailedError, LLMGateway, ProviderBinding
from src.llm.pricing import estimate_cost_usd, lookup
from src.llm.providers.base import LLMProvider


class FakeProvider(LLMProvider):
    def __init__(self, model: str, *, fail: bool = False, fail_after: int | None = None) -> None:
        self._model = model
        self._fail = fail
        self._fail_after = fail_after
        self.calls = 0

    @property
    def model_id(self) -> str:
        return self._model

    async def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3) -> str:
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self._model} is down")
        return f"answer from {self._model}"

    async def stream(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.3):
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self._model} is down")
        for index, token in enumerate(["one ", "two ", "three"]):
            if self._fail_after is not None and index == self._fail_after:
                raise RuntimeError("died mid-stream")
            yield token


def _gateway(*bindings: tuple[str, FakeProvider]) -> LLMGateway:
    return LLMGateway(
        role="small",
        chain=[ProviderBinding(name=n, provider=p) for n, p in bindings],
    )


@pytest.fixture(autouse=True)
def _flags():
    feature_flags.reset()
    yield
    feature_flags.reset()


class TestCompletion:
    @pytest.mark.asyncio
    async def test_primary_answers_when_healthy(self):
        primary = FakeProvider("gpt-4o-mini")
        secondary = FakeProvider("claude-haiku-4-5")
        gateway = _gateway(("openai", primary), ("anthropic", secondary))

        assert await gateway.complete("hi") == "answer from gpt-4o-mini"
        assert secondary.calls == 0, "fallback must not be called when primary works"

    @pytest.mark.asyncio
    async def test_falls_over_to_the_next_provider(self):
        """A single vendor outage degrades rather than failing the request."""
        broken = FakeProvider("gpt-4o-mini", fail=True)
        healthy = FakeProvider("claude-haiku-4-5")
        gateway = _gateway(("openai", broken), ("anthropic", healthy))

        assert await gateway.complete("hi") == "answer from claude-haiku-4-5"
        assert broken.calls == 1
        assert healthy.calls == 1

    @pytest.mark.asyncio
    async def test_all_failures_raise_with_every_error(self):
        """The first error is usually the real cause; later ones are
        consequences. Reporting only the last hides the diagnosis."""
        gateway = _gateway(
            ("openai", FakeProvider("a", fail=True)),
            ("anthropic", FakeProvider("b", fail=True)),
        )
        with pytest.raises(AllProvidersFailedError) as exc:
            await gateway.complete("hi")
        assert len(exc.value.failures) == 2
        assert "a is down" in str(exc.value)
        assert "b is down" in str(exc.value)

    @pytest.mark.asyncio
    async def test_fallback_can_be_switched_off(self):
        """Silent failover hides an outage behind an unexplained quality
        change. With the flag off, the outage is loud."""
        feature_flags.set_override("enable_llm_fallback", False)
        healthy = FakeProvider("claude-haiku-4-5")
        gateway = _gateway(
            ("openai", FakeProvider("gpt-4o-mini", fail=True)), ("anthropic", healthy)
        )

        with pytest.raises(AllProvidersFailedError):
            await gateway.complete("hi")
        assert healthy.calls == 0


class TestStreaming:
    @pytest.mark.asyncio
    async def test_streams_from_the_primary(self):
        gateway = _gateway(("openai", FakeProvider("gpt-4o-mini")))
        tokens = [t async for t in gateway.stream("hi")]
        assert "".join(tokens) == "one two three"

    @pytest.mark.asyncio
    async def test_fails_over_before_the_first_token(self):
        gateway = _gateway(
            ("openai", FakeProvider("gpt-4o-mini", fail=True)),
            ("anthropic", FakeProvider("claude-haiku-4-5")),
        )
        tokens = [t async for t in gateway.stream("hi")]
        assert "".join(tokens) == "one two three"

    @pytest.mark.asyncio
    async def test_midstream_failure_propagates_rather_than_splicing(self):
        """Switching provider after tokens have shipped would splice two
        different answers together."""
        gateway = _gateway(
            ("openai", FakeProvider("gpt-4o-mini", fail_after=1)),
            ("anthropic", FakeProvider("claude-haiku-4-5")),
        )
        with pytest.raises(RuntimeError, match="died mid-stream"):
            _ = [t async for t in gateway.stream("hi")]


class TestGatewayShape:
    def test_empty_chain_is_rejected(self):
        with pytest.raises(ValueError, match="empty provider chain"):
            LLMGateway(role="small", chain=[])

    def test_model_id_reports_the_primary(self):
        gateway = _gateway(
            ("openai", FakeProvider("gpt-4o-mini")), ("anthropic", FakeProvider("claude"))
        )
        assert gateway.model_id == "gpt-4o-mini"

    def test_describe_lists_the_chain(self):
        gateway = _gateway(
            ("openai", FakeProvider("gpt-4o-mini")), ("anthropic", FakeProvider("claude"))
        )
        described = gateway.describe()
        assert [c["provider"] for c in described["chain"]] == ["openai", "anthropic"]

    def test_gateway_is_a_drop_in_provider(self):
        """Implements LLMProvider, so every existing call site gains fallback
        and cost tracking with no change."""
        assert isinstance(_gateway(("openai", FakeProvider("m"))), LLMProvider)


class TestPricing:
    def test_known_model_is_priced(self):
        assert estimate_cost_usd("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)

    def test_longest_prefix_wins(self):
        """'gpt-4o-mini' must not be priced as 'gpt-4o'."""
        assert lookup("gpt-4o-mini") != lookup("gpt-4o")

    def test_dated_snapshots_match_their_base_model(self):
        assert lookup("gpt-4o-2024-08-06") == lookup("gpt-4o")

    def test_unknown_model_returns_none_not_zero(self):
        """A silent zero would understate spend on exactly the models nobody
        has reviewed the price of."""
        assert estimate_cost_usd("some-new-model", 1000, 1000) is None

    def test_local_models_price_at_zero(self):
        assert estimate_cost_usd("llama3.2:3b", 1000, 1000) == 0.0
