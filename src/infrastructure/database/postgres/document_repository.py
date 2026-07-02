from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.domain.entities.document import Document, DocumentMetadata, DocumentStatus
from src.domain.repositories.document_repository import DocumentRepository
from src.infrastructure.database.postgres.models import DocumentMetadataModel, DocumentModel


class PostgresDocumentRepository(DocumentRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, document: Document) -> Document:
        async with self._session_factory() as session:
            session.add(
                DocumentModel(
                    id=document.id,
                    user_id=document.user_id,
                    file_name=document.file_name,
                    file_type=document.file_type,
                    file_size_bytes=document.file_size_bytes,
                    file_path=document.file_path or None,
                    status=document.status.value,
                    page_count=document.page_count,
                    word_count=document.word_count,
                    loader_used=document.loader_used,
                )
            )
            await session.commit()
        return document

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocumentModel)
                .options(selectinload(DocumentModel.metadata_record))
                .where(DocumentModel.id == document_id)
            )
            model = result.scalar_one_or_none()
            return _to_entity(model) if model else None

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        size: int = 20,
        status: DocumentStatus | None = None,
        domain: str | None = None,
        file_type: str | None = None,
    ) -> tuple[list[Document], int]:
        async with self._session_factory() as session:
            stmt = select(DocumentModel).where(DocumentModel.user_id == user_id)
            if status is not None:
                stmt = stmt.where(DocumentModel.status == status.value)
            if file_type is not None:
                stmt = stmt.where(DocumentModel.file_type == file_type)
            if domain is not None:
                stmt = stmt.join(DocumentMetadataModel).where(DocumentMetadataModel.domain == domain)

            total = (
                await session.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar_one()

            stmt = (
                stmt.options(selectinload(DocumentModel.metadata_record))
                .order_by(DocumentModel.created_at.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
            result = await session.execute(stmt)
            documents = [_to_entity(model) for model in result.scalars().all()]
            return documents, total

    async def update(self, document: Document) -> Document:
        async with self._session_factory() as session:
            model = await session.get(DocumentModel, document.id)
            if model is None:
                raise ValueError(f"Document {document.id} not found")

            model.status = document.status.value
            model.error_message = document.error_message
            model.page_count = document.page_count
            model.word_count = document.word_count
            model.loader_used = document.loader_used
            model.indexed_at = document.indexed_at

            metadata_model = (
                await session.execute(
                    select(DocumentMetadataModel).where(
                        DocumentMetadataModel.document_id == document.id
                    )
                )
            ).scalar_one_or_none()
            if metadata_model is None:
                metadata_model = DocumentMetadataModel(document_id=document.id)
                session.add(metadata_model)
            metadata_model.summary = document.metadata.summary
            metadata_model.tags = document.metadata.tags
            metadata_model.domain = document.metadata.domain
            metadata_model.language = document.metadata.language
            metadata_model.entities = document.metadata.entities
            metadata_model.custom_metadata = document.metadata.custom_metadata

            await session.commit()
        return document

    async def delete(self, document_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            model = await session.get(DocumentModel, document_id)
            if model is None:
                return False
            await session.delete(model)
            await session.commit()
            return True


def _to_entity(model: DocumentModel) -> Document:
    document = Document(
        file_name=model.file_name,
        file_type=model.file_type,
        file_size_bytes=model.file_size_bytes,
        user_id=model.user_id,
        id=model.id,
        file_path=model.file_path or "",
        status=DocumentStatus(model.status),
        error_message=model.error_message,
        page_count=model.page_count,
        word_count=model.word_count,
        loader_used=model.loader_used,
        created_at=model.created_at,
        updated_at=model.updated_at,
        indexed_at=model.indexed_at,
    )
    if model.metadata_record:
        document.metadata = DocumentMetadata(
            summary=model.metadata_record.summary or "",
            tags=model.metadata_record.tags or [],
            domain=model.metadata_record.domain or "general",
            language=model.metadata_record.language or "en",
            entities=model.metadata_record.entities or [],
            custom_metadata=model.metadata_record.custom_metadata or {},
        )
    return document
