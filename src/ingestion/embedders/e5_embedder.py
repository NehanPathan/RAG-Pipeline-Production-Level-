from __future__ import annotations

import asyncio
from typing import Any

from src.ingestion.embedders.base import EmbeddingProvider


class E5EmbeddingProvider(EmbeddingProvider):
    """Local embedding via intfloat/e5-large-v2 (sentence-transformers) --
    no API cost, runs on CPU.

    E5 models are trained with an instruction prefix convention that
    materially affects retrieval quality: passages must be prefixed with
    "passage: " and queries with "query: " (per the model card's documented
    usage) -- this is handled here rather than left to callers, since
    forgetting it silently degrades embedding quality rather than erroring.

    The `SentenceTransformer` instance is injected for the same testability
    reason as `BGEEmbeddingProvider`/`BGEReranker`.
    """

    def __init__(self, model: Any, model_id: str = "intfloat/e5-large-v2") -> None:
        self._model = model
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [f"passage: {text}" for text in texts]
        embeddings = await asyncio.to_thread(
            self._model.encode, prefixed, normalize_embeddings=True, convert_to_numpy=True
        )
        return embeddings.tolist()

    async def embed_query(self, query: str) -> list[float]:
        embeddings = await asyncio.to_thread(
            self._model.encode, [f"query: {query}"], normalize_embeddings=True, convert_to_numpy=True
        )
        return embeddings[0].tolist()
