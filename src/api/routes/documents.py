from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel

from src.config import get_settings
from src.domain.entities.document import Document, DocumentStatus
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

    # Create document record (stub — in production this comes from DI + auth)
    doc_id = uuid.uuid4()
    logger.info("document_upload_received", doc_id=str(doc_id), file=file.filename, size=len(content))

    # Background ingestion task (pipeline wired via DI in production)
    background_tasks.add_task(_run_ingestion_stub, doc_id, file_path, file_ext)

    documents_ingested.labels(file_type=file_ext, status="pending").inc()

    return UploadResponse(
        document_id=str(doc_id),
        file_name=file.filename or "",
        status="processing",
        message="Document accepted for ingestion. Poll /documents/{id} for status.",
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(page: int = 1, size: int = 20) -> DocumentListResponse:
    # Stub — full implementation wires DocumentRepository from DI
    return DocumentListResponse(items=[], total=0, page=page, size=size)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str) -> DocumentResponse:
    raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str) -> dict:
    logger.info("document_delete_requested", doc_id=document_id)
    return {"document_id": document_id, "deleted_chunks": 0, "message": "Document deleted."}


async def _run_ingestion_stub(doc_id: uuid.UUID, file_path: Path, file_type: str) -> None:
    """Placeholder — replaced by full IngestionPipeline.ingest() when DI is wired."""
    logger.info("ingestion_background_task_started", doc_id=str(doc_id), file=str(file_path))
    # Full pipeline call: await pipeline.ingest(document, file_path)
