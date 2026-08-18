from unittest.mock import MagicMock

import numpy as np
import pytest

from src.ingestion.embedders.e5_embedder import E5EmbeddingProvider


@pytest.fixture
def fake_model():
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = 1024
    model.encode.return_value = np.array([[0.4, 0.5, 0.6]])
    return model


@pytest.fixture
def provider(fake_model) -> E5EmbeddingProvider:
    return E5EmbeddingProvider(model=fake_model, model_id="intfloat/e5-large-v2")


async def test_embed_texts_prefixes_with_passage(provider, fake_model):
    await provider.embed_texts(["refund policy"])
    args, _ = fake_model.encode.call_args
    assert args[0] == ["passage: refund policy"]


async def test_embed_query_prefixes_with_query(provider, fake_model):
    await provider.embed_query("what is the refund window?")
    args, _ = fake_model.encode.call_args
    assert args[0] == ["query: what is the refund window?"]


async def test_embed_texts_empty_input_short_circuits(provider, fake_model):
    result = await provider.embed_texts([])
    assert result == []
    fake_model.encode.assert_not_called()


async def test_embed_texts_returns_plain_lists(provider):
    result = await provider.embed_texts(["a"])
    assert result == [[0.4, 0.5, 0.6]]
