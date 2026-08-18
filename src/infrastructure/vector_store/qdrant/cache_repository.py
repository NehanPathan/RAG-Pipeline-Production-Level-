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
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="source_document_ids",
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
            logger.info(
                "qdrant_cache_collection_created",
                collection=self._collection_name,
                vector_size=vector_size,
            )
        else:
            # The index is needed for delete_by_document on collections that
            # already existed before cache invalidation was added.
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name="source_document_ids",
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                logger.info("qdrant_cache_index_skipped", error=str(exc))

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
                # Denormalized from the citations so invalidation is a single
                # indexed filter rather than a scan that parses every payload.
                "source_document_ids": entry.source_document_ids,
            },
        )
        await self._client.upsert(collection_name=self._collection_name, points=[point])
        logger.info("qdrant_cache_stored", collection=self._collection_name)

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        """Drop every cached answer that cited the given document.

        Entries stored before `source_document_ids` existed carry no such
        field and therefore survive this filter. That residue is bounded by
        the cache's own churn and is called out in the data card rather than
        papered over -- clearing the whole cache on every delete would be the
        alternative, and it trades a small, decaying leak for a guaranteed
        latency cliff on every deletion.
        """
        try:
            await self._client.delete(
                collection_name=self._collection_name,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="source_document_ids",
                                match=qdrant_models.MatchValue(value=str(document_id)),
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:
            logger.warning(
                "qdrant_cache_delete_skipped", document_id=str(document_id), error=str(exc)
            )
            return 0
        logger.info("qdrant_cache_invalidated", document_id=str(document_id))
        return 0
