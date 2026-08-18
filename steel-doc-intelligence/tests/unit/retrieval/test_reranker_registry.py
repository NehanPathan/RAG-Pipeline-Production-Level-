from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.retrieval.rerankers.bge_reranker import BGEReranker
from src.retrieval.rerankers.cohere_reranker import CohereReranker
from src.retrieval.rerankers.registry import get_reranker


def test_get_reranker_returns_bge_when_configured():
    settings = Settings(reranker_provider="bge", bge_reranker_model="BAAI/bge-reranker-large")

    with patch("src.retrieval.rerankers.registry.CrossEncoder") as mock_cross_encoder_cls:
        mock_cross_encoder_cls.return_value = MagicMock()
        reranker = get_reranker(settings)

    assert isinstance(reranker, BGEReranker)
    assert reranker.name == "BAAI/bge-reranker-large"
    mock_cross_encoder_cls.assert_called_once_with("BAAI/bge-reranker-large")


def test_get_reranker_returns_cohere_when_configured():
    settings = Settings(
        reranker_provider="cohere",
        cohere_api_key="test-key",
        cohere_reranker_model="rerank-english-v3.0",
    )

    with patch("src.retrieval.rerankers.registry.cohere.AsyncClientV2") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        reranker = get_reranker(settings)

    assert isinstance(reranker, CohereReranker)
    assert reranker.name == "rerank-english-v3.0"
    mock_client_cls.assert_called_once_with(api_key="test-key")


def test_get_reranker_raises_for_unsupported_provider():
    settings = Settings(reranker_provider="unknown")

    with pytest.raises(ValueError, match="Unsupported reranker provider"):
        get_reranker(settings)
