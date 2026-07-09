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
    get_intelligence_repository,
    get_search_repository,
    get_vector_repository,
)
from src.config import get_settings
from src.domain.entities.document import Document, DocumentChunk
from src.domain.value_objects.document_intelligence import DocumentIntelligenceSummary
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


class SimilarityEdgeResponse(BaseModel):
    chunk_id_a: str
    chunk_id_b: str
    similarity: float


class LayoutSummaryResponse(BaseModel):
    headings: list[dict]
    outline: list[dict]
    tables_count: int
    figures_count: int
    lists_count: int
    forms_count: int
    footnotes_count: int


class ChunkResponse(BaseModel):
    id: str
    parent_chunk_id: str | None
    chunk_type: str
    content: str
    position: int
    page_number: int | None
    section_title: str | None
    heading_level: int | None
    semantic_cluster: int | None
    ocr_confidence: float | None
    language: str | None
    token_count: int
    embedding_model: str


class ChunkListResponse(BaseModel):
    items: list[ChunkResponse]
    total: int


class DocumentIntelligenceResponse(BaseModel):
    document_id: str
    ocr_engine: str
    ocr_ran: bool
    ocr_confidence_avg: float | None
    ocr_processing_time_ms: float
    ocr_language: str | None
    embedding_model_chunking: str
    embedding_model_retrieval: str
    layout: LayoutSummaryResponse
    semantic_graph: list[SimilarityEdgeResponse]
    created_at: str | None


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


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(document_id: str) -> ChunkListResponse:
    chunk_repo = get_chunk_repository()
    chunks = await chunk_repo.get_by_document(uuid.UUID(document_id))
    return ChunkListResponse(items=[_to_chunk_response(c) for c in chunks], total=len(chunks))


@router.get("/documents/{document_id}/intelligence", response_model=DocumentIntelligenceResponse)
async def get_document_intelligence(document_id: str) -> DocumentIntelligenceResponse:
    intelligence_repo = get_intelligence_repository()
    summary = await intelligence_repo.get_by_document(uuid.UUID(document_id))
    if summary is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="No Document Intelligence data for this document (not yet indexed, or indexed before Phase 4A).",
        )
    return _to_intelligence_response(summary)


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


def _to_chunk_response(chunk: DocumentChunk) -> ChunkResponse:
    return ChunkResponse(
        id=str(chunk.id),
        parent_chunk_id=str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
        chunk_type=chunk.chunk_type.value,
        content=chunk.content,
        position=chunk.position,
        page_number=chunk.chunk_metadata.page_number,
        section_title=chunk.chunk_metadata.section_title,
        heading_level=chunk.chunk_metadata.heading_level,
        semantic_cluster=chunk.chunk_metadata.semantic_cluster,
        ocr_confidence=chunk.chunk_metadata.ocr_confidence,
        language=chunk.chunk_metadata.language,
        token_count=chunk.token_count,
        embedding_model=chunk.embedding_model,
    )


def _to_intelligence_response(summary: DocumentIntelligenceSummary) -> DocumentIntelligenceResponse:
    return DocumentIntelligenceResponse(
        document_id=str(summary.document_id),
        ocr_engine=summary.ocr_engine,
        ocr_ran=summary.ocr_ran,
        ocr_confidence_avg=summary.ocr_confidence_avg,
        ocr_processing_time_ms=summary.ocr_processing_time_ms,
        ocr_language=summary.ocr_language,
        embedding_model_chunking=summary.embedding_model_chunking,
        embedding_model_retrieval=summary.embedding_model_retrieval,
        layout=LayoutSummaryResponse(
            headings=summary.layout.headings,
            outline=summary.layout.outline,
            tables_count=summary.layout.tables_count,
            figures_count=summary.layout.figures_count,
            lists_count=summary.layout.lists_count,
            forms_count=summary.layout.forms_count,
            footnotes_count=summary.layout.footnotes_count,
        ),
        semantic_graph=[
            SimilarityEdgeResponse(
                chunk_id_a=str(edge.chunk_id_a),
                chunk_id_b=str(edge.chunk_id_b),
                similarity=edge.similarity,
            )
            for edge in summary.semantic_graph
        ],
        created_at=summary.created_at.isoformat() if summary.created_at else None,
    )


async def _run_ingestion(pipeline: IngestionPipeline, document: Document, file_path: Path) -> None:
    await pipeline.ingest(document, file_path)
