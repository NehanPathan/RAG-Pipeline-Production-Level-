from __future__ import annotations

from src.domain.repositories.cache_repository import SemanticCacheRepository
from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.ingestion.embedders.base import EmbeddingProvider
from src.monitoring.stage_tracer import traced_stage


class SemanticCache:
    """Embeds the incoming query and checks the semantic cache for a
    near-duplicate previously-answered query above `score_threshold`.

    Uses the same EmbeddingProvider as document-chunk embedding (same
    model -> comparable vector space), not a separate cache-specific
    embedder, so lookups and the original ingestion embeddings are
    directly comparable.
    """

    def __init__(
        self,
        repository: SemanticCacheRepository,
        embedding_provider: EmbeddingProvider,
        score_threshold: float = 0.95,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._score_threshold = score_threshold

    async def lookup(self, query: str) -> SemanticCacheEntry | None:
        async with traced_stage("semantic_cache_lookup", query=query) as stage:
            embedding = await self._embedding_provider.embed_query(query)
            entry = await self._repository.find_similar(embedding, self._score_threshold)
            stage.set_result(cache_hit=entry is not None)
            return entry

    async def store(self, query: str, answer: str, citations: list[dict], model_used: str) -> None:
        async with traced_stage("semantic_cache_store", query=query) as stage:
            embedding = await self._embedding_provider.embed_query(query)
            entry = SemanticCacheEntry(
                query_text=query, answer=answer, citations=citations, model_used=model_used
            )
            await self._repository.store(embedding, entry)
            stage.set_result(stored=True)
