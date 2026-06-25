from unittest.mock import MagicMock, patch

import pytest

from src.api.dependencies import get_query_pipeline, reset_query_pipeline
from src.retrieval.pipeline import QueryPipeline


@pytest.fixture(autouse=True)
def _reset():
    reset_query_pipeline()
    yield
    reset_query_pipeline()


def test_get_query_pipeline_constructs_without_error():
    # Default reranker_provider="bge" would otherwise load a real
    # multi-GB cross-encoder model from HuggingFace Hub.
    with patch("src.retrieval.rerankers.registry.CrossEncoder") as mock_cross_encoder_cls:
        mock_cross_encoder_cls.return_value = MagicMock()
        pipeline = get_query_pipeline()

    assert isinstance(pipeline, QueryPipeline)


def test_get_query_pipeline_is_singleton():
    with patch("src.retrieval.rerankers.registry.CrossEncoder") as mock_cross_encoder_cls:
        mock_cross_encoder_cls.return_value = MagicMock()
        first = get_query_pipeline()
        second = get_query_pipeline()

    assert first is second


def test_reset_query_pipeline_forces_rebuild():
    with patch("src.retrieval.rerankers.registry.CrossEncoder") as mock_cross_encoder_cls:
        mock_cross_encoder_cls.return_value = MagicMock()
        first = get_query_pipeline()
        reset_query_pipeline()
        second = get_query_pipeline()

    assert first is not second
