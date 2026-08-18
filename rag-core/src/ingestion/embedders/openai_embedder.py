from __future__ import annotations

import hashlib

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.ingestion.embedders.base import EmbeddingProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import embedding_cache_hits

logger = get_logger(__name__)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-large",
        dimensions: int = 3072,
        batch_size: int = 100,
        cache=None,
        cache_ttl: int = 86400,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._cache = cache
        self._cache_ttl = cache_ttl

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Check cache for each text
        for i, text in enumerate(texts):
            cached = await self._get_cached(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append([])  # placeholder
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Embed uncached texts in batches
        if uncached_texts:
            embeddings = await self._embed_batch(uncached_texts)
            for list_idx, embedding in zip(uncached_indices, embeddings, strict=True):
                results[list_idx] = embedding
                await self._set_cached(texts[list_idx], embedding)

        return results

    async def embed_query(self, query: str) -> list[float]:
        cached = await self._get_cached(query)
        if cached is not None:
            return cached
        embeddings = await self._embed_batch([query])
        result = embeddings[0]
        await self._set_cached(query, result)
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        all_embeddings = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimensions,
            )
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
            logger.debug(
                "openai_embedded_batch",
                batch_size=len(batch),
                model=self._model,
            )
        return all_embeddings

    def _cache_key(self, text: str) -> str:
        content_hash = hashlib.sha256(f"{self._model}:{text}".encode()).hexdigest()
        return f"embedding:{content_hash}"

    async def _get_cached(self, text: str) -> list[float] | None:
        # Every cached read -- ingestion batches and query embeddings alike --
        # funnels through here, so this is the one place that can report the
        # true hit rate. That rate is a direct cost control: each miss is a
        # billed embedding call.
        if self._cache is None:
            embedding_cache_hits.labels(result="disabled").inc()
            return None
        data = await self._cache.get(self._cache_key(text))
        embedding_cache_hits.labels(result="hit" if data is not None else "miss").inc()
        if data is not None:
            return data
        return None

    async def _set_cached(self, text: str, embedding: list[float]) -> None:
        if self._cache is None:
            return
        await self._cache.set(self._cache_key(text), embedding, self._cache_ttl)
