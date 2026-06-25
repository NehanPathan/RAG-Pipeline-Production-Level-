from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from src.domain.repositories.cache_repository import SemanticCacheRepository
from src.domain.value_objects.cache_entry import SemanticCacheEntry
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class QdrantSemanticCacheRepository(SemanticCacheRepository):
    """Semantic query cache backed by a dedicated Qdrant collection,
    separate from `document_chunks` — gives native ANN similarity search
    instead of a client-side bounded scan over a capped Redis index (the
    originally-proposed design; see Phase 3 design review §6 decision 2).

    Entries are keyed by a fresh UUID per store(), not by query text —
    lookup is purely by vector similarity against `score_threshold`, so two
    differently-phrased queries with the same intent can still hit.
    """

    def __init__(self, client: AsyncQdrantClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    async def create_collection_if_not_exists(self, vector_size: int) -> None:
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if self._collection_name not in names:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=vector_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            logger.info(
                "qdrant_cache_collection_created",
                collection=self._collection_name,
                vector_size=vector_size,
            )

    async def find_similar(
        self, query_embedding: list[float], score_threshold: float
    ) -> SemanticCacheEntry | None:
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=query_embedding,
            limit=1,
            score_threshold=score_threshold,
            with_payload=True,
        )
        if not response.points:
            return None

        payload = response.points[0].payload or {}
        return SemanticCacheEntry(
            query_text=payload.get("query_text", ""),
            answer=payload.get("answer", ""),
            citations=payload.get("citations") or [],
            model_used=payload.get("model_used", ""),
        )

    async def store(self, query_embedding: list[float], entry: SemanticCacheEntry) -> None:
        point = qdrant_models.PointStruct(
            id=str(uuid.uuid4()),
            vector=query_embedding,
            payload={
                "query_text": entry.query_text,
                "answer": entry.answer,
                "citations": entry.citations,
                "model_used": entry.model_used,
                "created_at": entry.created_at.isoformat(),
            },
        )
        await self._client.upsert(collection_name=self._collection_name, points=[point])
        logger.info("qdrant_cache_stored", collection=self._collection_name)
