from unittest.mock import MagicMock, patch

import pytest

import src.ingestion.embedders.registry as registry_module
from src.config import Settings
from src.ingestion.embedders.bge_embedder import BGEEmbeddingProvider
from src.ingestion.embedders.e5_embedder import E5EmbeddingProvider
from src.ingestion.embedders.openai_embedder import OpenAIEmbeddingProvider
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
    registry_module._bge_model = None
    registry_module._e5_model = None
    yield
    registry_module._bge_model = None
    registry_module._e5_model = None


def test_retrieval_role_defaults_to_openai():
    provider = get_embedding_provider(_settings(), role="retrieval")
    assert isinstance(provider, OpenAIEmbeddingProvider)


def test_chunking_role_defaults_to_bge_m3():
    fake_st = MagicMock()
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_st):
        provider = get_embedding_provider(_settings(), role="chunking")
    assert isinstance(provider, BGEEmbeddingProvider)


def test_chunking_role_can_select_e5_large():
    fake_st = MagicMock()
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_st):
        provider = get_embedding_provider(
            _settings(chunking_embedding_provider="e5_large"), role="chunking"
        )
    assert isinstance(provider, E5EmbeddingProvider)


def test_sentence_transformer_model_is_cached_across_calls():
    fake_ctor = MagicMock(return_value=MagicMock())
    with patch("sentence_transformers.SentenceTransformer", fake_ctor):
        get_embedding_provider(_settings(), role="chunking")
        get_embedding_provider(_settings(), role="chunking")
    assert fake_ctor.call_count == 1


def test_unsupported_provider_raises():
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        get_embedding_provider(_settings(chunking_embedding_provider="nonexistent"), role="chunking")


def test_build_embedding_strategy_bundles_both_roles():
    fake_st = MagicMock()
    with patch("sentence_transformers.SentenceTransformer", return_value=fake_st):
        strategy = build_embedding_strategy(_settings())
    assert isinstance(strategy.retrieval_provider, OpenAIEmbeddingProvider)
    assert isinstance(strategy.chunking_provider, BGEEmbeddingProvider)
