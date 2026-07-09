from unittest.mock import MagicMock

import numpy as np
import pytest

from src.ingestion.embedders.bge_embedder import BGEEmbeddingProvider


@pytest.fixture
def fake_model():
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = 1024
    model.encode.return_value = np.array([[0.1, 0.2, 0.3]])
    return model


@pytest.fixture
def provider(fake_model) -> BGEEmbeddingProvider:
    return BGEEmbeddingProvider(model=fake_model, model_id="BAAI/bge-m3")


def test_model_id_and_dimensions(provider, fake_model):
    assert provider.model_id == "BAAI/bge-m3"
    assert provider.dimensions == 1024


async def test_embed_texts_returns_plain_lists(provider):
    result = await provider.embed_texts(["hello world"])
    assert result == [[0.1, 0.2, 0.3]]


async def test_embed_texts_empty_input_short_circuits(provider, fake_model):
    result = await provider.embed_texts([])
    assert result == []
    fake_model.encode.assert_not_called()


async def test_embed_texts_normalizes_embeddings(provider, fake_model):
    await provider.embed_texts(["a"])
    _, kwargs = fake_model.encode.call_args
    assert kwargs["normalize_embeddings"] is True


async def test_embed_query_delegates_to_embed_texts(provider):
    result = await provider.embed_query("hello")
    assert result == [0.1, 0.2, 0.3]
