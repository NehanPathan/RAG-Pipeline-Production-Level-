from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.repositories.document_repository import ChunkRepository
from src.domain.value_objects.provenance import Region
from src.domain.value_objects.sensitivity import Sensitivity
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
                        sensitivity=chunk.sensitivity.value,
                        # Denormalized fields (migration 0004). Persisted so
                        # Postgres can rebuild a chunk faithfully -- without
                        # these, re-indexing from Postgres wipes the tenant
                        # filter out of Elasticsearch.
                        user_id=chunk.user_id,
                        domain=chunk.domain,
                        tags=chunk.tags or None,
                        file_type=chunk.file_type,
                        document_name=chunk.document_name,
                        project_id=chunk.project_id,
                        project_number=chunk.project_number,
                        drawing_id=chunk.drawing_id,
                        drawing_number=chunk.drawing_number,
                        revision_label=chunk.revision_label,
                        is_latest=chunk.is_latest,
                        content_kind=chunk.chunk_metadata.content_kind,
                        regions=[r.to_dict() for r in chunk.chunk_metadata.regions],
                        region_precision=chunk.chunk_metadata.region_precision,
                        layers=sorted(set(chunk.chunk_metadata.layers)),
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

    async def set_sensitivity(self, document_id: uuid.UUID, sensitivity: Sensitivity) -> int:
        """Reclassify every chunk of a document in one statement.

        A bulk UPDATE rather than load-mutate-save: reclassification touches
        every chunk of the document and none of the other fields, so there is
        nothing to be gained from materialising hundreds of entities, and
        doing so was how the denormalized search-backend fields got lost.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                sa_update(DocumentChunkModel)
                .where(DocumentChunkModel.document_id == document_id)
                .values(sensitivity=sensitivity.value)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        async with self._session_factory() as session:
            ids = (
                (
                    await session.execute(
                        select(DocumentChunkModel.id).where(
                            DocumentChunkModel.document_id == document_id
                        )
                    )
                )
                .scalars()
                .all()
            )
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
            content_kind=model.content_kind,
            # A malformed rectangle costs a highlight, not an answer.
            regions=[
                r for r in (Region.from_dict(raw) for raw in (model.regions or [])) if r is not None
            ],
            region_precision=model.region_precision,
            layers=list(model.layers or []),
        ),
        qdrant_point_id=model.qdrant_point_id,
        created_at=model.created_at,
        sensitivity=Sensitivity.parse(model.sensitivity, Sensitivity.INTERNAL),
        user_id=model.user_id,
        domain=model.domain,
        tags=list(model.tags) if model.tags else [],
        file_type=model.file_type,
        document_name=model.document_name,
        project_id=model.project_id,
        project_number=model.project_number,
        drawing_id=model.drawing_id,
        drawing_number=model.drawing_number,
        revision_label=model.revision_label,
        # Rows predating revisions have no flag and are current by
        # definition: nothing supersedes them.
        is_latest=bool(model.is_latest) if model.is_latest is not None else True,
    )
