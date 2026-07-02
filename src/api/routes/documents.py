from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel

from src.api.dependencies import (
    DEFAULT_USER_ID,
    get_chunk_repository,
    get_document_repository,
    get_ingestion_pipeline,
    get_search_repository,
    get_vector_repository,
)
from src.config import get_settings
from src.domain.entities.document import Document
from src.ingestion.pipeline import IngestionPipeline
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import documents_ingested

router = APIRouter()
logger = get_logger(__name__)

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


class DocumentResponse(BaseModel):
    id: str
    file_name: str
    file_type: str
    status: str
    page_count: int | None = None
    word_count: int | None = None
    domain: str | None = None
    tags: list[str] = []
    indexed_at: str | None = None
    created_at: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    size: int


class UploadResponse(BaseModel):
    document_id: str
    file_name: str
    status: str
    message: str


@router.post("/documents", response_model=UploadResponse, status_code=http_status.HTTP_202_ACCEPTED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    domain: str = Form(default=""),
    tags: str = Form(default=""),
) -> UploadResponse:
    settings = get_settings()

    # Validate file type
    file_ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if file_ext not in settings.allowed_file_types_list:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file_ext}' is not supported. Allowed: {settings.allowed_file_types}",
        )

    # Validate file size
    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File size exceeds maximum of {settings.max_file_size_mb}MB",
        )

    # Save to temp location
    file_path = UPLOAD_DIR / f"{uuid.uuid4()}_{file.filename}"
    file_path.write_bytes(content)

    document = Document(
        file_name=file.filename or "unnamed",
        file_type=file_ext,
        file_size_bytes=len(content),
        user_id=DEFAULT_USER_ID,
        file_path=str(file_path),
    )
    if domain:
        document.metadata.domain = domain
    if tags:
        document.metadata.tags = [t.strip() for t in tags.split(",") if t.strip()]

    document_repo = get_document_repository()
    await document_repo.save(document)

    logger.info("document_upload_received", doc_id=str(document.id), file=file.filename, size=len(content))

    background_tasks.add_task(_run_ingestion, get_ingestion_pipeline(), document, file_path)

    documents_ingested.labels(file_type=file_ext, status="pending").inc()

    return UploadResponse(
        document_id=str(document.id),
        file_name=file.filename or "",
        status="processing",
        message="Document accepted for ingestion. Poll /documents/{id} for status.",
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(page: int = 1, size: int = 20) -> DocumentListResponse:
    document_repo = get_document_repository()
    documents, total = await document_repo.list_by_user(DEFAULT_USER_ID, page=page, size=size)
    return DocumentListResponse(
        items=[_to_response(d) for d in documents],
        total=total,
        page=page,
        size=size,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str) -> DocumentResponse:
    document_repo = get_document_repository()
    document = await document_repo.get_by_id(uuid.UUID(document_id))
    if document is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _to_response(document)


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str) -> dict:
    doc_uuid = uuid.UUID(document_id)
    document_repo = get_document_repository()
    chunk_repo = get_chunk_repository()

    deleted_chunks = len(await chunk_repo.get_by_document(doc_uuid))

    # Vector/search indexes are separate stores from Postgres and aren't
    # covered by the documents.id FK cascade, so they need explicit cleanup
    # before (or regardless of) the Postgres row going away.
    await get_vector_repository().delete_by_document(doc_uuid)
    await get_search_repository().delete_by_document(doc_uuid)

    deleted = await document_repo.delete(doc_uuid)
    if not deleted:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")

    logger.info("document_delete_requested", doc_id=document_id, deleted_chunks=deleted_chunks)
    return {"document_id": document_id, "deleted_chunks": deleted_chunks, "message": "Document deleted."}


def _to_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=str(document.id),
        file_name=document.file_name,
        file_type=document.file_type,
        status=document.status.value,
        page_count=document.page_count,
        word_count=document.word_count,
        domain=document.metadata.domain,
        tags=document.metadata.tags,
        indexed_at=document.indexed_at.isoformat() if document.indexed_at else None,
        created_at=document.created_at.isoformat(),
    )


async def _run_ingestion(pipeline: IngestionPipeline, document: Document, file_path: Path) -> None:
    await pipeline.ingest(document, file_path)
