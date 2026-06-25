from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from src.config import get_settings
from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.repositories.vector_repository import (
    ScoredChunk,
    VectorRepository,
    VectorSearchFilter,
)
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


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
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="user_id",
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="domain",
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="tags",
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
            await self._client.create_payload_index(
                collection_name=self._collection_name,
                field_name="file_type",
                field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            )
            logger.info("qdrant_collection_created", collection=self._collection_name, vector_size=vector_size)

    async def upsert_batch(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        points = [
            qdrant_models.PointStruct(
                id=str(chunk.id),
                vector=chunk.embedding,
                payload={
                    "chunk_id": str(chunk.id),
                    "document_id": str(chunk.document_id),
                    "user_id": str(chunk.user_id) if chunk.user_id else None,
                    "content": chunk.content,
                    "chunk_type": chunk.chunk_type.value,
                    "position": chunk.position,
                    "token_count": chunk.token_count,
                    "page_number": chunk.chunk_metadata.page_number,
                    "section": chunk.chunk_metadata.section,
                    "contains_table": chunk.chunk_metadata.contains_table,
                    "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                    "domain": chunk.domain,
                    "tags": chunk.tags,
                    "file_type": chunk.file_type,
                    "document_name": chunk.document_name,
                },
            )
            for chunk in chunks
            if chunk.has_embedding()
        ]
        await self._client.upsert(collection_name=self._collection_name, points=points)
        logger.info("qdrant_upserted", count=len(points), collection=self._collection_name)

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
            chunk = DocumentChunk(
                id=uuid.UUID(str(result.id)),
                document_id=uuid.UUID(payload["document_id"]),
                content=payload.get("content", ""),
                position=payload.get("position", 0),
                chunk_type=ChunkType(payload.get("chunk_type", "child")),
                token_count=payload.get("token_count", 0),
                parent_chunk_id=(
                    uuid.UUID(payload["parent_chunk_id"]) if payload.get("parent_chunk_id") else None
                ),
                chunk_metadata=ChunkMetadata(
                    page_number=payload.get("page_number"),
                    section=payload.get("section"),
                    contains_table=payload.get("contains_table", False),
                ),
                user_id=uuid.UUID(payload["user_id"]) if payload.get("user_id") else None,
                domain=payload.get("domain"),
                tags=payload.get("tags") or [],
                file_type=payload.get("file_type"),
                document_name=payload.get("document_name"),
            )
            scored_chunks.append(ScoredChunk(chunk=chunk, score=result.score, rank=rank + 1))

        return scored_chunks

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        result = await self._client.delete(
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
        deleted = result.result.deleted if result.result else 0
        logger.info("qdrant_deleted", document_id=str(document_id), count=deleted)
        return deleted

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
        if filters.user_id:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="user_id",
                    match=qdrant_models.MatchValue(value=str(filters.user_id)),
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
        return qdrant_models.Filter(must=conditions) if conditions else None


def create_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )
