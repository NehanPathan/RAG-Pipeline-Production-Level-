from __future__ import annotations

import re
import uuid

from src.domain.repositories.cache_repository import SemanticCacheRepository
from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.ingestion.embedders.base import EmbeddingProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import semantic_cache_lookups
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> list[str]:
    """The numeric literals in a question, normalised for comparison.

    Thousands separators are stripped first so "1,000" and "1000" are the same
    number, and a trailing ".0" is dropped so "30" and "30.0" are too.
    """
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", text)
    out = []
    for raw in _NUMBER.findall(cleaned):
        value = raw.rstrip("0").rstrip(".") if "." in raw else raw
        out.append(value or "0")
    return sorted(out)


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

            # An embedding barely moves when a single digit changes, so
            # "revenue in 2023" and "revenue in 2024" sit well above the 0.95
            # threshold while asking factually different questions -- the cache
            # would confidently answer one with the other's answer. Similarity
            # cannot express "these numbers must be identical", so the numbers
            # are compared literally.
            #
            # This is deliberately strict: a question whose numbers differ at
            # all is treated as a miss, including when one side has no numbers
            # at all. That costs a recomputation; the alternative costs a
            # wrong answer.
            if entry is not None and _numbers(query) != _numbers(entry.query_text):
                logger.info(
                    "semantic_cache_numeric_mismatch",
                    asked=_numbers(query),
                    cached=_numbers(entry.query_text),
                )
                semantic_cache_lookups.labels(result="rejected_numeric_mismatch").inc()
                stage.set_result(cache_hit=False, rejected="numeric_mismatch")
                return None

            stage.set_result(cache_hit=entry is not None)
            return entry

    async def store(self, query: str, answer: str, citations: list[dict], model_used: str) -> None:
        async with traced_stage("semantic_cache_store", query=query) as stage:
            embedding = await self._embedding_provider.embed_query(query)
            entry = SemanticCacheEntry(
                query_text=query, answer=answer, citations=citations, model_used=model_used
            )
            await self._repository.store(embedding, entry)
            stage.set_result(stored=True, sources=len(entry.source_document_ids))

    async def invalidate_document(self, document_id: uuid.UUID) -> None:
        """Drop cached answers derived from a document that no longer exists."""
        async with traced_stage(
            "semantic_cache_invalidate", document_id=str(document_id)
        ) as stage:
            await self._repository.delete_by_document(document_id)
            stage.set_result(invalidated=True)
