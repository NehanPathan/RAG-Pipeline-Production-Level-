from __future__ import annotations

import asyncio
from typing import Any

from src.ingestion.embedders.base import EmbeddingProvider


class BGEEmbeddingProvider(EmbeddingProvider):
    """Local embedding via BAAI/bge-m3 (sentence-transformers) -- no API
    cost, runs on CPU.

    The `SentenceTransformer` instance is injected rather than constructed
    here, so unit tests substitute a fake with the same `.encode()` surface
    instead of downloading a real multi-GB model from HuggingFace Hub --
    same pattern as `BGEReranker`'s injected `CrossEncoder`.
    `SentenceTransformer.encode()` is a synchronous, CPU/GPU-bound call --
    run via `asyncio.to_thread` so it doesn't block the event loop.
    """

    def __init__(self, model: Any, model_id: str = "BAAI/bge-m3") -> None:
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
        embeddings = await asyncio.to_thread(
            self._model.encode, texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return embeddings.tolist()

    async def embed_query(self, query: str) -> list[float]:
        embeddings = await self.embed_texts([query])
        return embeddings[0]
