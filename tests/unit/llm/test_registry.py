import pytest

from src.config import Settings
from src.llm.providers.openai_provider import OpenAIProvider
from src.llm.registry import get_llm_provider


@pytest.fixture
def settings():
    return Settings(
        openai_api_key="sk-test",
        small_llm_provider="openai",
        large_llm_provider="openai",
        openai_small_model="gpt-4o-mini",
        openai_large_model="gpt-4o",
    )


def test_get_llm_provider_small_role_uses_small_model(settings):
    provider = get_llm_provider(settings, "small")

    assert isinstance(provider, OpenAIProvider)
    assert provider.model_id == "gpt-4o-mini"


def test_get_llm_provider_large_role_uses_large_model(settings):
    provider = get_llm_provider(settings, "large")

    assert isinstance(provider, OpenAIProvider)
    assert provider.model_id == "gpt-4o"


def test_get_llm_provider_raises_for_unsupported_provider(settings):
    settings.small_llm_provider = "anthropic"

    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        get_llm_provider(settings, "small")
