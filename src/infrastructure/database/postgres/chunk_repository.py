from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.repositories.document_repository import ChunkRepository
from src.infrastructure.database.postgres.models import DocumentChunkModel


class PostgresChunkRepository(ChunkRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_batch(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        if not chunks:
            return chunks
        async with self._session_factory() as session:
            for chunk in chunks:
                # Qdrant uses the chunk's own id as its point id (see
                # QdrantVectorRepository.upsert_batch), so this is set here
                # rather than waiting on a round-trip from the vector store.
                chunk.qdrant_point_id = chunk.id
                session.add(
                    DocumentChunkModel(
                        id=chunk.id,
                        document_id=chunk.document_id,
                        parent_chunk_id=chunk.parent_chunk_id,
                        chunk_type=chunk.chunk_type.value,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                        position=chunk.position,
                        token_count=chunk.token_count,
                        page_number=chunk.chunk_metadata.page_number,
                        section=chunk.chunk_metadata.section,
                        contains_table=chunk.chunk_metadata.contains_table,
                        embedding_model=chunk.embedding_model or None,
                        qdrant_point_id=chunk.qdrant_point_id,
                        section_title=chunk.chunk_metadata.section_title,
                        heading_level=chunk.chunk_metadata.heading_level,
                        semantic_cluster=chunk.chunk_metadata.semantic_cluster,
                        ocr_confidence=chunk.chunk_metadata.ocr_confidence,
                        language=chunk.chunk_metadata.language,
                    )
                )
            await session.commit()
        return chunks

    async def get_by_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentChunkModel)
                .where(DocumentChunkModel.document_id == document_id)
                .order_by(DocumentChunkModel.position)
            )
            return [_to_entity(model) for model in result.scalars().all()]

    async def get_by_ids(self, chunk_ids: list[uuid.UUID]) -> list[DocumentChunk]:
        if not chunk_ids:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentChunkModel).where(DocumentChunkModel.id.in_(chunk_ids))
            )
            return [_to_entity(model) for model in result.scalars().all()]

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            ids = (
                await session.execute(
                    select(DocumentChunkModel.id).where(
                        DocumentChunkModel.document_id == document_id
                    )
                )
            ).scalars().all()
            if ids:
                await session.execute(
                    sa_delete(DocumentChunkModel).where(
                        DocumentChunkModel.document_id == document_id
                    )
                )
                await session.commit()
            return len(ids)


def _to_entity(model: DocumentChunkModel) -> DocumentChunk:
    return DocumentChunk(
        document_id=model.document_id,
        content=model.content,
        position=model.position,
        id=model.id,
        parent_chunk_id=model.parent_chunk_id,
        chunk_type=ChunkType(model.chunk_type),
        token_count=model.token_count or 0,
        embedding_model=model.embedding_model or "",
        chunk_metadata=ChunkMetadata(
            page_number=model.page_number,
            section=model.section,
            contains_table=model.contains_table,
            section_title=model.section_title,
            heading_level=model.heading_level,
            semantic_cluster=model.semantic_cluster,
            ocr_confidence=model.ocr_confidence,
            language=model.language,
        ),
        qdrant_point_id=model.qdrant_point_id,
        created_at=model.created_at,
    )
