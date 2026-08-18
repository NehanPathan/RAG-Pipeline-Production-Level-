from unittest.mock import MagicMock, patch

import pytest

import src.ingestion.embedders.registry as registry_module
from src.config import Settings
from src.ingestion.embedders.langchain_embedder import LangChainEmbeddingProvider
from src.ingestion.embedders.registry import build_embedding_strategy, get_embedding_provider


def _settings(**overrides) -> Settings:
    # A dummy key is required, not incidental: the OpenAI client raises at
    # construction when it can find no credentials anywhere. These tests are
    # about which provider the registry selects, so they supply one rather
    # than depending on the ambient environment having a real key.
    overrides.setdefault("openai_api_key", "sk-test-not-a-real-key")
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def reset_model_cache():
    registry_module.reset_local_model_cache()
    yield
    registry_module.reset_local_model_cache()


def _patched_hf():
    """Patch the local-model constructor so no multi-GB download happens."""
    return patch("langchain_huggingface.HuggingFaceEmbeddings", return_value=MagicMock())


def test_retrieval_role_defaults_to_openai():
    provider = get_embedding_provider(_settings(), role="retrieval")

    assert isinstance(provider, LangChainEmbeddingProvider)
    assert provider.model_id == "text-embedding-3-large"
    assert provider.dimensions == 3072


def test_chunking_role_defaults_to_bge_m3():
    with _patched_hf():
        provider = get_embedding_provider(_settings(), role="chunking")

    assert provider.model_id == "BAAI/bge-m3"
    assert provider.dimensions == 1024


def test_e5_can_be_selected_for_the_chunking_role():
    with _patched_hf():
        provider = get_embedding_provider(
            _settings(chunking_embedding_provider="e5_large"), role="chunking"
        )

    assert provider.model_id == "intfloat/e5-large-v2"


def test_e5_applies_its_instruction_prefixes():
    """E5 is trained to see "query: " and "passage: ". Omitting them degrades
    retrieval quality silently rather than erroring, so the registry must
    configure them rather than leaving it to callers."""
    with _patched_hf():
        provider = get_embedding_provider(
            _settings(chunking_embedding_provider="e5_large"), role="chunking"
        )

    assert provider._query_prefix == "query: "
    assert provider._document_prefix == "passage: "


def test_openai_is_configured_without_prefixes():
    provider = get_embedding_provider(_settings(), role="retrieval")

    assert provider._query_prefix == ""
    assert provider._document_prefix == ""


def test_local_model_is_constructed_once_per_process():
    """These are multi-hundred-MB downloads; constructing one per call would
    make every request that touches the chunking role pay for it."""
    with _patched_hf() as hf:
        get_embedding_provider(_settings(), role="chunking")
        get_embedding_provider(_settings(), role="chunking")

    assert hf.call_count == 1


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        get_embedding_provider(_settings(embedding_provider="nope"), role="retrieval")


def test_build_embedding_strategy_bundles_both_roles():
    with _patched_hf():
        strategy = build_embedding_strategy(_settings())

    assert strategy.retrieval_provider.model_id == "text-embedding-3-large"
    assert strategy.chunking_provider.model_id == "BAAI/bge-m3"
