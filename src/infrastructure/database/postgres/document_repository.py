from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.domain.entities.document import Document, DocumentMetadata, DocumentStatus
from src.domain.repositories.document_repository import DocumentRepository
from src.domain.value_objects.sensitivity import Sensitivity
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
                    sensitivity=document.sensitivity.value,
                    retention_until=document.retention_until,
                    project_id=document.project_id,
                    drawing_id=document.drawing_id,
                    revision_label=document.revision_label,
                    revision_index=document.revision_index,
                    revision_date=document.revision_date,
                    revision_note=document.revision_note,
                    is_latest=document.is_latest,
                    superseded_by_document_id=document.superseded_by_document_id,
                    superseded_at=document.superseded_at,
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
        sensitivity_in: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[Document], int]:
        async with self._session_factory() as session:
            stmt = select(DocumentModel).where(DocumentModel.user_id == user_id)
            if status is not None:
                stmt = stmt.where(DocumentModel.status == status.value)
            if file_type is not None:
                stmt = stmt.where(DocumentModel.file_type == file_type)
            if domain is not None:
                stmt = stmt.join(DocumentMetadataModel).where(DocumentMetadataModel.domain == domain)

            if search:
                # Wildcards in user input are escaped so a search for "%"
                # finds a percent sign rather than matching every document,
                # and "_" is a literal underscore rather than "any character".
                # The `escape=` argument is required: without it Postgres has
                # no escape character and the backslashes are matched
                # literally, so the escaping silently does nothing.
                pattern = (
                    search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                stmt = stmt.where(
                    DocumentModel.file_name.ilike(f"%{pattern}%", escape="\\")
                )

            if sensitivity_in is not None:
                # Applied before the count and before LIMIT/OFFSET, so the
                # total reflects only what this caller may read and pages are
                # never short. A NULL column predates classification and is
                # read as the policy default (`internal`), matching
                # Sensitivity.parse -- so legacy rows are admitted only when
                # the caller's clearance actually covers that default.
                clause = DocumentModel.sensitivity.in_(sensitivity_in)
                if Sensitivity.INTERNAL.value in sensitivity_in:
                    clause = or_(clause, DocumentModel.sensitivity.is_(None))
                stmt = stmt.where(clause)

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
            model.sensitivity = document.sensitivity.value
            model.retention_until = document.retention_until
            # Revision state changes after ingestion -- RegisterRevision
            # supersedes the previous current sheet through this path -- so
            # these must be part of the update, not only the insert.
            model.project_id = document.project_id
            model.drawing_id = document.drawing_id
            model.revision_label = document.revision_label
            model.revision_index = document.revision_index
            model.revision_date = document.revision_date
            model.revision_note = document.revision_note
            model.is_latest = document.is_latest
            model.superseded_by_document_id = document.superseded_by_document_id
            model.superseded_at = document.superseded_at

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
        # A NULL column means the row predates classification; INTERNAL is
        # the fail-closed reading, matching Sensitivity.parse's contract.
        sensitivity=Sensitivity.parse(model.sensitivity, Sensitivity.INTERNAL),
        retention_until=model.retention_until,
        project_id=model.project_id,
        drawing_id=model.drawing_id,
        revision_label=model.revision_label,
        revision_index=model.revision_index,
        revision_date=model.revision_date,
        revision_note=model.revision_note,
        is_latest=bool(model.is_latest) if model.is_latest is not None else True,
        superseded_by_document_id=model.superseded_by_document_id,
        superseded_at=model.superseded_at,
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
