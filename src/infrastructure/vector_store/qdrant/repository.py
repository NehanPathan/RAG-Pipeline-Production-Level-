from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from src.config import get_settings
from src.domain.entities.document import DocumentChunk
from src.domain.repositories.vector_repository import (
    ScoredChunk,
    VectorRepository,
    VectorSearchFilter,
)
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.serialization.chunk_payload import chunk_to_payload, payload_to_chunk
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Every payload field a filter runs against, in one list, so collection
# creation and the startup backfill cannot disagree. A field indexed at
# creation but forgotten in the backfill would full-scan forever on any
# deployment that already had the collection -- correct results, silently
# terrible latency, and nothing in the logs to say so.
INDEXED_PAYLOAD_FIELDS = (
    "user_id",
    "domain",
    "tags",
    "file_type",
    "sensitivity",
    "entity_canonicals",
    "project_id",
    "drawing_id",
    "is_latest",
)


class QdrantVectorRepository(VectorRepository):
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
            # Governance MAP: the classification filter runs on every single
            # query, so `sensitivity` needs an index as much as user_id does.
            for field_name in INDEXED_PAYLOAD_FIELDS:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )
            logger.info("qdrant_collection_created", collection=self._collection_name, vector_size=vector_size)

    async def ensure_payload_indexes(self) -> None:
        """Add payload indexes to a collection that already existed.

        `create_collection_if_not_exists` only indexes fields at creation
        time, so a deployment that already had `document_chunks` would filter
        on any later-added field without an index — correct, but a full scan
        per query. Called from startup; safe to run repeatedly.
        """
        for field_name in INDEXED_PAYLOAD_FIELDS:
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                # Already-indexed and missing-collection both land here and
                # are both fine: the first is the steady state, the second
                # means no documents exist yet and creation will index it.
                logger.info("qdrant_payload_index_skipped", field=field_name, error=str(exc))

    async def upsert_batch(self, chunks: list[DocumentChunk], batch_size: int = 100) -> None:
        if not chunks:
            return
        points = [
            qdrant_models.PointStruct(
                id=str(chunk.id),
                vector=chunk.embedding,
                payload=chunk_to_payload(chunk),
            )
            for chunk in chunks
            if chunk.has_embedding()
        ]
        # Large documents can produce hundreds of high-dimension vectors;
        # upserting them in one request risks write timeouts, so chunk the
        # request itself rather than relying on a single huge payload.
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            await self._client.upsert(collection_name=self._collection_name, points=batch)
        logger.info("qdrant_upserted", count=len(points), collection=self._collection_name)

    async def set_payload_by_document(
        self, document_id: uuid.UUID, payload: dict[str, Any]
    ) -> None:
        """Update selected payload keys on every point of one document.

        A whole-point upsert cannot serve this: `upsert_batch` skips chunks
        with no embedding, and a caller that reloaded chunks from Postgres
        has none — so the upsert produced an empty point list and the change
        never reached the vector store at all, silently. `set_payload` needs
        no vector and touches only the named keys.
        """
        try:
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload=payload,
                points=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=str(document_id)),
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:
            logger.warning(
                "qdrant_set_payload_failed", document_id=str(document_id), error=str(exc)
            )
            return
        logger.info(
            "qdrant_payload_updated",
            document_id=str(document_id),
            fields=sorted(payload.keys()),
        )

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        filters: VectorSearchFilter | None = None,
    ) -> list[ScoredChunk]:
        qdrant_filter = self._build_filter(filters)
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        scored_chunks = []
        for rank, result in enumerate(response.points):
            payload = result.payload or {}
            chunk = payload_to_chunk(payload, uuid.UUID(str(result.id)))
            scored_chunks.append(ScoredChunk(chunk=chunk, score=result.score, rank=rank + 1))

        return scored_chunks

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        try:
            await self._client.delete(
                collection_name=self._collection_name,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=str(document_id)),
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:
            # Collection may not exist if the document failed before vector indexing
            logger.warning("qdrant_delete_skipped", document_id=str(document_id), error=str(exc))
            return 0
        logger.info("qdrant_deleted", document_id=str(document_id))
        return 0

    async def get_collection_info(self) -> dict:
        info = await self._client.get_collection(self._collection_name)
        return {
            "name": self._collection_name,
            "vectors_count": info.vectors_count,
            "status": info.status,
        }

    def _build_filter(self, filters: VectorSearchFilter | None) -> qdrant_models.Filter | None:
        if filters is None:
            return None
        conditions = []
        # Reachability: owner OR project member. Expressed as one `should`
        # group inside `must`, so it narrows the result set as a unit -- two
        # separate `must` conditions would mean "owned AND in a project",
        # which hides every document a caller owns outside their projects.
        if filters.user_id or filters.project_ids:
            reachable: list[qdrant_models.Condition] = []
            if filters.user_id:
                reachable.append(
                    qdrant_models.FieldCondition(
                        key="user_id",
                        match=qdrant_models.MatchValue(value=str(filters.user_id)),
                    )
                )
            if filters.project_ids:
                reachable.append(
                    qdrant_models.FieldCondition(
                        key="project_id",
                        match=qdrant_models.MatchAny(
                            any=[str(p) for p in filters.project_ids]
                        ),
                    )
                )
            conditions.append(qdrant_models.Filter(should=reachable))

        if filters.latest_only:
            # Points written before revisions existed have no `is_latest`
            # key. They are current by definition -- there is nothing that
            # supersedes them -- so a bare match would hide the entire
            # pre-revision corpus.
            conditions.append(
                qdrant_models.Filter(
                    should=[
                        qdrant_models.FieldCondition(
                            key="is_latest", match=qdrant_models.MatchValue(value=True)
                        ),
                        qdrant_models.IsNullCondition(
                            is_null=qdrant_models.PayloadField(key="is_latest")
                        ),
                    ]
                )
            )
        if filters.domain:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="domain",
                    match=qdrant_models.MatchValue(value=filters.domain),
                )
            )
        if filters.tags:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="tags",
                    match=qdrant_models.MatchAny(any=filters.tags),
                )
            )
        if filters.file_type:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="file_type",
                    match=qdrant_models.MatchValue(value=filters.file_type),
                )
            )
        if filters.document_ids:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="document_id",
                    match=qdrant_models.MatchAny(any=[str(d) for d in filters.document_ids]),
                )
            )
        if filters.sensitivity_in is not None:
            # `should_be_null` alongside the allow-list: points indexed before
            # the field existed have no `sensitivity` key at all, and a bare
            # MatchAny would exclude them silently. They are admitted here only
            # when the caller's clearance covers the INTERNAL default that
            # SensitivityGuard will re-check them against post-retrieval.
            allow_null = Sensitivity.INTERNAL.value in filters.sensitivity_in
            match_condition = qdrant_models.FieldCondition(
                key="sensitivity",
                match=qdrant_models.MatchAny(any=list(filters.sensitivity_in)),
            )
            if allow_null:
                conditions.append(
                    qdrant_models.Filter(
                        should=[
                            match_condition,
                            qdrant_models.IsNullCondition(
                                is_null=qdrant_models.PayloadField(key="sensitivity")
                            ),
                        ]
                    )
                )
            else:
                conditions.append(match_condition)
        return qdrant_models.Filter(must=conditions) if conditions else None


def create_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=60,
    )
