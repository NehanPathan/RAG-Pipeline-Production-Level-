import pytest

from src.config import Settings
from src.governance.policy import reset_policy
from src.llm.gateway import LLMGateway
from src.llm.providers.base import LLMProvider
from src.llm.providers.langchain_provider import LangChainChatProvider
from src.llm.registry import build_gateway, get_llm_provider, providers


@pytest.fixture(autouse=True)
def _reset_policy():
    reset_policy()
    yield
    reset_policy()


@pytest.fixture
def settings():
    return Settings(
        openai_api_key="sk-test",
        small_llm_provider="openai",
        large_llm_provider="openai",
        openai_small_model="gpt-4o-mini",
        openai_large_model="gpt-4o",
        llm_fallback_providers="openai",
    )


class TestRoleSelection:
    def test_small_role_uses_the_small_model(self, settings):
        provider = get_llm_provider(settings, "small")
        assert provider.model_id == "gpt-4o-mini"

    def test_large_role_uses_the_large_model(self, settings):
        provider = get_llm_provider(settings, "large")
        assert provider.model_id == "gpt-4o"

    def test_returns_a_gateway_that_is_a_drop_in_provider(self, settings):
        """The gateway implements LLMProvider, which is what let every
        existing call site gain fallback and cost tracking unchanged."""
        provider = get_llm_provider(settings, "small")
        assert isinstance(provider, LLMGateway)
        assert isinstance(provider, LLMProvider)

    def test_primary_of_the_chain_is_the_configured_provider(self, settings):
        gateway = build_gateway(settings, "small")
        assert gateway.primary.name == "openai"
        assert isinstance(gateway.primary.provider, LangChainChatProvider)


class TestFallbackChain:
    def test_chain_puts_the_primary_first(self, settings):
        settings.small_llm_provider = "openai"
        settings.llm_fallback_providers = "ollama,openai"
        gateway = build_gateway(settings, "small")
        assert [b.name for b in gateway.chain][0] == "openai"

    def test_duplicates_are_removed(self, settings):
        """A chain of ["openai", "openai"] would retry the same failing
        provider and look like a working fallback."""
        settings.llm_fallback_providers = "openai,openai"
        gateway = build_gateway(settings, "small")
        assert [b.name for b in gateway.chain] == ["openai"]

    def test_unknown_provider_in_the_chain_is_skipped(self, settings):
        """One bad entry must not deny the role its working providers."""
        settings.llm_fallback_providers = "not-a-real-vendor,openai"
        gateway = build_gateway(settings, "small")
        assert "not-a-real-vendor" not in [b.name for b in gateway.chain]
        assert "openai" in [b.name for b in gateway.chain]

    def test_no_usable_provider_raises_with_a_diagnosis(self, settings):
        settings.small_llm_provider = "not-a-real-vendor"
        settings.llm_fallback_providers = "also-not-real"
        with pytest.raises(ValueError, match="No usable LLM provider"):
            build_gateway(settings, "small")


class TestPolicyEnforcement:
    def test_unapproved_provider_is_excluded_from_the_chain(self, settings, monkeypatch):
        """C-GOV-01: an unapproved model must not reach production through a
        code path that forgot to ask.

        The policy singleton is built from process settings rather than the
        object handed to build_gateway -- there is one policy in force per
        process by design -- so the settings source is patched rather than the
        argument mutated.
        """
        settings.governance_allowed_llm_providers = "anthropic"
        settings.small_llm_provider = "openai"
        settings.llm_fallback_providers = "openai"

        monkeypatch.setattr("src.config.get_settings", lambda: settings)
        reset_policy()

        with pytest.raises(ValueError, match="No usable LLM provider"):
            build_gateway(settings, "small")


class TestProviderRegistry:
    def test_expected_providers_are_registered(self):
        assert {"openai", "anthropic", "ollama", "openrouter", "azure"} <= set(
            providers.names()
        )

    def test_registry_describes_itself(self):
        described = {p["name"] for p in providers.describe()}
        assert "openai" in described

    def test_duplicate_registration_is_rejected(self):
        """Silently overwriting would make import order decide which
        implementation runs."""
        with pytest.raises(ValueError, match="already registered"):

            @providers.register("openai")
            def _duplicate(settings, role):  # pragma: no cover
                ...
