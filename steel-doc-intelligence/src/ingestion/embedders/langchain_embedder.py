"""Adapter from any LangChain `Embeddings` to this codebase's port.

Replaces three hand-written embedding clients (an AsyncOpenAI wrapper with
its own batching and retry, and two SentenceTransformer wrappers) with
`OpenAIEmbeddings` and `HuggingFaceEmbeddings`, which already handle
batching, retries, thread offloading and device placement.

Two things are kept rather than delegated:

* **The Redis cache and its hit-rate metric.** Every cached read funnels
  through here, so this is the one place that can report a true hit rate --
  and that rate is a direct cost control, since each miss is a billed
  embedding call. LangChain's `CacheBackedEmbeddings` ships in
  `langchain-classic` in v1 and reports nothing.
* **The E5 prefix convention.** E5 models are trained to see "query: " and
  "passage: " prefixes, and omitting them silently degrades retrieval
  quality rather than erroring. `HuggingFaceEmbeddings` has no equivalent,
  so the prefixes stay here, applied on the correct side of the
  query/document split.
"""

from __future__ import annotations

import hashlib

from langchain_core.embeddings import Embeddings

from src.ingestion.embedders.base import EmbeddingProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import embedding_cache_hits

logger = get_logger(__name__)


class LangChainEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        embeddings: Embeddings,
        model_id: str,
        dimensions: int,
        cache: object | None = None,
        cache_ttl: int = 86400,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._embeddings = embeddings
        self._model_id = model_id
        self._dimensions = dimensions
        self._cache = cache
        self._cache_ttl = cache_ttl
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float]] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for index, text in enumerate(texts):
            cached = await self._get_cached(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append([])  # placeholder, filled in below
                uncached_indices.append(index)
                uncached_texts.append(self._document_prefix + text)

        if uncached_texts:
            embeddings = await self._embeddings.aembed_documents(uncached_texts)
            for position, embedding in zip(uncached_indices, embeddings, strict=True):
                results[position] = embedding
                await self._set_cached(texts[position], embedding)

        return results

    async def embed_query(self, query: str) -> list[float]:
        cached = await self._get_cached(query)
        if cached is not None:
            return cached
        embedding = await self._embeddings.aembed_query(self._query_prefix + query)
        await self._set_cached(query, embedding)
        return embedding

    def _cache_key(self, text: str) -> str:
        # Keyed on the model as well as the text: two models produce different
        # vectors for the same string, and a shared key would serve one
        # model's vectors to the other -- silently, and into a vector store
        # whose whole premise is that its vectors are comparable.
        content_hash = hashlib.sha256(f"{self._model_id}:{text}".encode()).hexdigest()
        return f"embedding:{content_hash}"

    async def _get_cached(self, text: str) -> list[float] | None:
        if self._cache is None:
            embedding_cache_hits.labels(result="disabled").inc()
            return None
        data = await self._cache.get(self._cache_key(text))  # type: ignore[attr-defined]
        embedding_cache_hits.labels(result="hit" if data is not None else "miss").inc()
        return data if data is not None else None

    async def _set_cached(self, text: str, embedding: list[float]) -> None:
        if self._cache is None:
            return
        await self._cache.set(self._cache_key(text), embedding, self._cache_ttl)  # type: ignore[attr-defined]
