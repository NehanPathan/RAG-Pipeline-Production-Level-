from unittest.mock import MagicMock, patch

import pytest

import src.api.dependencies as dependencies_module
import src.ingestion.embedders.registry as embedding_registry_module
from src.api.dependencies import get_query_pipeline, reset_query_pipeline
from src.ingestion.pipeline import IngestionPipeline
from src.retrieval.pipeline import QueryPipeline


@pytest.fixture(autouse=True)
def _reset():
    reset_query_pipeline()
    dependencies_module._ingestion_pipeline = None
    embedding_registry_module._bge_model = None
    embedding_registry_module._e5_model = None
    yield
    reset_query_pipeline()
    dependencies_module._ingestion_pipeline = None
    embedding_registry_module._bge_model = None
    embedding_registry_module._e5_model = None


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


def test_get_ingestion_pipeline_constructs_without_error():
    # Default chunking_embedding_provider="bge_m3" would otherwise load a
    # real multi-GB SentenceTransformer model from HuggingFace Hub.
    with patch("sentence_transformers.SentenceTransformer", return_value=MagicMock()):
        pipeline = dependencies_module.get_ingestion_pipeline()

    assert isinstance(pipeline, IngestionPipeline)


def test_get_ingestion_pipeline_is_singleton():
    with patch("sentence_transformers.SentenceTransformer", return_value=MagicMock()):
        first = dependencies_module.get_ingestion_pipeline()
        second = dependencies_module.get_ingestion_pipeline()

    assert first is second
