from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from pydantic import BaseModel

from src.api.dependencies import (
    get_chunk_repository,
    get_document_repository,
    get_ingestion_pipeline,
    get_intelligence_repository,
    get_query_pipeline,
    get_search_repository,
    get_vector_repository,
)
from src.api.dependencies_rate_limit import rate_limit_role
from src.config import get_settings
from src.domain.entities.document import Document, DocumentChunk
from src.domain.value_objects.document_intelligence import DocumentIntelligenceSummary
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.policy import get_policy
from src.governance.rbac import Principal, Role, get_principal, require_role
from src.governance.runtime_flags import get_flags
from src.ingestion.pipeline import IngestionPipeline
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import access_denied, documents_ingested

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
    sensitivity: str
    retention_until: str | None = None


class ReclassifyRequest(BaseModel):
    sensitivity: str
    reason: str = ""


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
    sensitivity: str = Form(default=""),
    principal: Principal = Depends(rate_limit_role(
        "upload", Role.ANALYST, Role.STEWARD, Role.ADMIN
    )),
) -> UploadResponse:
    """Accept a document for ingestion, classified at the point of entry.

    Classification happens here rather than after indexing because the window
    between the two is exactly when an unclassified document is retrievable.
    An omitted `sensitivity` resolves to the policy default (`internal`), and
    a caller cannot classify a document above their own clearance -- otherwise
    upload would be a way to create data you are then unable to review.
    """
    settings = get_settings()
    policy = get_policy()

    flags = await get_flags()
    if not flags.ingestion_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingestion is temporarily disabled by an administrator.",
        )

    classification = Sensitivity.parse(sensitivity, policy.default_sensitivity)
    if not classification.readable_with(principal.clearance):
        access_denied.labels(reason="upload_above_clearance").inc()
        await audit_record(
            action=AuditAction.ACCESS_DENIED,
            actor_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document",
            outcome=AuditOutcome.DENIED,
            reason=f"attempted to classify upload as '{classification.value}'",
            control_id="C-MAP-01",
        )
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f"Cannot classify a document as '{classification.value}' "
            f"with clearance '{principal.clearance.value}'.",
        )

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
        user_id=principal.user_id,
        file_path=str(file_path),
    )
    document.classify(classification, retention_days=policy.retention_days)
    if domain:
        document.metadata.domain = domain
    if tags:
        document.metadata.tags = [t.strip() for t in tags.split(",") if t.strip()]

    document_repo = get_document_repository()
    await document_repo.save(document)

    logger.info(
        "document_upload_received",
        doc_id=str(document.id),
        file=file.filename,
        size=len(content),
        sensitivity=classification.value,
    )
    await audit_record(
        action=AuditAction.DOCUMENT_UPLOADED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="document",
        resource_id=str(document.id),
        outcome=AuditOutcome.COMPLETED,
        after={
            "file_name": document.file_name,
            "sensitivity": classification.value,
            "retention_until": document.retention_until,
        },
    )

    background_tasks.add_task(_run_ingestion, get_ingestion_pipeline(), document, file_path)

    documents_ingested.labels(file_type=file_ext, status="pending").inc()

    return UploadResponse(
        document_id=str(document.id),
        file_name=file.filename or "",
        status="processing",
        message="Document accepted for ingestion. Poll /documents/{id} for status.",
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    principal: Principal = Depends(get_principal),
) -> DocumentListResponse:
    document_repo = get_document_repository()
    # Clearance is pushed into the query, so `total` counts only what this
    # caller may read and every page is full. Filtering the page after the
    # fact (the previous behaviour) disclosed an accurate count of documents
    # the caller had no right to know existed, and returned short pages whose
    # length revealed how many had been withheld.
    documents, total = await document_repo.list_by_user(
        principal.user_id,
        page=page,
        size=size,
        sensitivity_in=Sensitivity.values_at_or_below(principal.clearance),
        search=search,
    )
    return DocumentListResponse(
        items=[_to_response(d) for d in documents],
        total=total,
        page=page,
        size=size,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    principal: Principal = Depends(get_principal),
) -> DocumentResponse:
    document = await _load_readable_document(document_id, principal)
    return _to_response(document)


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(
    document_id: str,
    principal: Principal = Depends(get_principal),
) -> ChunkListResponse:
    # Clearance is checked against the parent document before any chunk text
    # is read: this endpoint returns full chunk content, so skipping the
    # check here would make it a way around the retrieval-time filter.
    await _load_readable_document(document_id, principal)
    chunk_repo = get_chunk_repository()
    chunks = await chunk_repo.get_by_document(uuid.UUID(document_id))
    return ChunkListResponse(items=[_to_chunk_response(c) for c in chunks], total=len(chunks))


@router.get("/documents/{document_id}/intelligence", response_model=DocumentIntelligenceResponse)
async def get_document_intelligence(
    document_id: str,
    principal: Principal = Depends(get_principal),
) -> DocumentIntelligenceResponse:
    await _load_readable_document(document_id, principal)
    intelligence_repo = get_intelligence_repository()
    summary = await intelligence_repo.get_by_document(uuid.UUID(document_id))
    if summary is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="No Document Intelligence data for this document (not yet indexed, or indexed before Phase 4A).",
        )
    return _to_intelligence_response(summary)


@router.put("/documents/{document_id}/classification", response_model=DocumentResponse)
async def reclassify_document(
    document_id: str,
    body: ReclassifyRequest,
    principal: Principal = Depends(require_role(Role.STEWARD, Role.ADMIN)),
) -> DocumentResponse:
    """Change a document's classification and re-propagate it to every store.

    Steward/admin only, and always audited with both the old and new values:
    reclassifying downward is the single action in this system that can turn
    restricted data into broadly-readable data, so it must be attributable.

    Re-indexing is required because classification is denormalized onto every
    chunk in Postgres, Qdrant and Elasticsearch. Updating only the document
    row would leave the retrieval filters reading the old label -- the
    classification would appear changed in the UI while behaving unchanged.

    The propagation is a *partial field update*, not a re-index, because a
    re-index here was silently destructive in both stores:

    * Qdrant's `upsert_batch` skips chunks with no embedding. Chunks reloaded
      from Postgres have none (vectors live only in Qdrant), so the point
      list was empty and the new classification never reached the vector
      store -- the exact opposite of what this endpoint claims to do.
    * Elasticsearch's `index_batch` replaces the whole document body. The
      denormalized `user_id`/`domain`/`tags`/`file_type`/`document_name`
      fields have no Postgres columns to be restored from, so they were
      rewritten as null and the document dropped out of its own owner's BM25
      filter -- it became unfindable by keyword search for the person who
      uploaded it.

    Both are also ~100x cheaper as partial updates on a 500-chunk document.
    """
    policy = get_policy()
    document = await _load_readable_document(document_id, principal)
    new_classification = Sensitivity.parse(body.sensitivity, policy.default_sensitivity)
    previous = document.sensitivity

    if not new_classification.readable_with(principal.clearance):
        access_denied.labels(reason="reclassify_above_clearance").inc()
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f"Cannot set classification '{new_classification.value}' "
            f"with clearance '{principal.clearance.value}'.",
        )

    document.classify(new_classification, retention_days=policy.retention_days)
    await get_document_repository().update(document)

    doc_uuid = uuid.UUID(document_id)
    chunks_reclassified = await get_chunk_repository().set_sensitivity(
        doc_uuid, new_classification
    )
    await get_vector_repository().set_payload_by_document(
        doc_uuid, {"sensitivity": new_classification.value}
    )
    await get_search_repository().update_fields_by_document(
        doc_uuid, {"sensitivity": new_classification.value}
    )

    # Cached answers were produced under the previous classification and may
    # now be readable by the wrong audience.
    await get_query_pipeline().invalidate_cached_document(doc_uuid)

    await audit_record(
        action=AuditAction.DOCUMENT_RECLASSIFIED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="document",
        resource_id=document_id,
        outcome=AuditOutcome.COMPLETED,
        before={"sensitivity": previous.value},
        after={"sensitivity": new_classification.value},
        reason=body.reason,
        control_id="C-MAP-02",
    )
    logger.warning(
        "document_reclassified",
        doc_id=document_id,
        before=previous.value,
        after=new_classification.value,
        chunks_reclassified=chunks_reclassified,
    )
    return _to_response(document)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    principal: Principal = Depends(require_role(Role.STEWARD, Role.ADMIN)),
) -> dict:
    """Remove a document from every store that holds it.

    Deletion is privileged and audited because it is irreversible, and it now
    covers the semantic cache as well: previously a deleted document's
    answers kept being served from cache, which meant deletion did not
    actually delete.
    """
    doc_uuid = uuid.UUID(document_id)
    document_repo = get_document_repository()
    chunk_repo = get_chunk_repository()

    document = await _load_readable_document(document_id, principal)
    deleted_chunks = len(await chunk_repo.get_by_document(doc_uuid))

    # Vector/search indexes are separate stores from Postgres and aren't
    # covered by the documents.id FK cascade, so they need explicit cleanup
    # before (or regardless of) the Postgres row going away.
    await get_vector_repository().delete_by_document(doc_uuid)
    await get_search_repository().delete_by_document(doc_uuid)
    await get_query_pipeline().invalidate_cached_document(doc_uuid)

    deleted = await document_repo.delete(doc_uuid)
    if not deleted:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")

    logger.info("document_delete_requested", doc_id=document_id, deleted_chunks=deleted_chunks)
    await audit_record(
        action=AuditAction.DOCUMENT_DELETED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="document",
        resource_id=document_id,
        outcome=AuditOutcome.COMPLETED,
        before={
            "file_name": document.file_name,
            "sensitivity": document.sensitivity.value,
            "chunks": deleted_chunks,
        },
    )
    return {"document_id": document_id, "deleted_chunks": deleted_chunks, "message": "Document deleted."}


async def _load_readable_document(document_id: str, principal: Principal) -> Document:
    """Fetch a document, 404-ing when the principal may not read it.

    404 rather than 403 on the clearance failure is intentional: a 403 would
    confirm that a document with that id exists, which is itself information
    the caller is not cleared for. The audit entry records the true reason.
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document_id is not a valid UUID.",
        )

    document = await get_document_repository().get_by_id(doc_uuid)
    if document is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not document.sensitivity.readable_with(principal.clearance):
        access_denied.labels(reason="document_above_clearance").inc()
        await audit_record(
            action=AuditAction.ACCESS_DENIED,
            actor_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document",
            resource_id=document_id,
            outcome=AuditOutcome.DENIED,
            reason=f"document is '{document.sensitivity.value}', "
            f"principal holds '{principal.clearance.value}'",
            control_id="C-MAP-01",
        )
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")

    return document


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
        sensitivity=document.sensitivity.value,
        retention_until=(
            document.retention_until.isoformat() if document.retention_until else None
        ),
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
