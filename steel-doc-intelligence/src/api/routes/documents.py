from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi import status as http_status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from src.api.dependencies import (
    enqueue_ingestion,
    get_chunk_repository,
    get_document_repository,
    get_intelligence_repository,
    get_job_repository,
    get_project_repository,
    get_query_pipeline,
    get_register_revision,
    get_search_repository,
    get_vector_repository,
)
from src.api.dependencies_rate_limit import rate_limit_role
from src.config import get_settings
from src.domain.entities.document import Document, DocumentChunk, DocumentStatus
from src.domain.repositories.blob_store import BlobTooLargeError, document_key
from src.domain.value_objects.document_intelligence import DocumentIntelligenceSummary
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.policy import get_policy
from src.governance.rbac import Principal, Role, get_principal, require_role
from src.governance.runtime_flags import get_flags
from src.infrastructure.storage import get_blob_store
from src.jobs.models import JobStatus, JobType
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
    # Why ingestion stopped: the failure for FAILED, the screening reason
    # for QUARANTINED. A reviewer deciding whether to release a held
    # document needs to see what held it, and without this the UI could
    # only offer the button and not the grounds for pressing it.
    error_message: str | None = None


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


async def _iter_upload(file: UploadFile, chunk_size: int = 1024 * 1024):
    """Yield the upload in chunks so it is never held whole in memory."""
    while chunk := await file.read(chunk_size):
        yield chunk


@router.post("/documents", response_model=UploadResponse, status_code=http_status.HTTP_202_ACCEPTED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    domain: str = Form(default=""),
    tags: str = Form(default=""),
    sensitivity: str = Form(default=""),
    project_id: str = Form(default=""),
    # The drawing this file is a revision of. Supplied by the uploader, who
    # is reading the title block, and therefore authoritative over the
    # extractor's guess -- see `_register_drawing_revision`, which stands
    # down when these are given.
    drawing_number: str = Form(default=""),
    revision_label: str = Form(default=""),
    principal: Principal = Depends(
        rate_limit_role("upload", Role.ANALYST, Role.STEWARD, Role.ADMIN)
    ),
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
        ) from None

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
        ) from None

    # Validate file type
    file_ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if file_ext not in settings.allowed_file_types_list:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file_ext}' is not supported. Allowed: {settings.allowed_file_types}",
        ) from None

    # Stream to durable storage, enforcing the size limit as it goes.
    #
    # The previous code read the whole upload into memory and checked its
    # size afterwards, so `max_file_size_bytes` bounded what was *accepted*
    # but not what a request could *allocate* -- a 2 GB upload was a 2 GB
    # allocation regardless of the limit. Enforcing mid-stream makes the
    # limit bound both.
    document = Document(
        file_name=file.filename or "unnamed",
        file_type=file_ext,
        file_size_bytes=0,
        user_id=principal.user_id,
    )
    blob_store = get_blob_store(settings)
    key = document_key(str(document.id), file.filename or "unnamed")
    try:
        stored = await blob_store.put_stream(
            key,
            _iter_upload(file),
            content_type=file.content_type or "application/octet-stream",
            max_bytes=settings.max_file_size_bytes,
        )
    except BlobTooLargeError:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File size exceeds maximum of {settings.max_file_size_mb}MB",
        ) from None

    document.file_size_bytes = stored.size_bytes
    document.file_path = stored.key

    # Ingestion needs a real path: Docling, ezdxf, pdf2image and Tesseract all
    # take filenames, not streams. The blob is the durable copy; this is a
    # working copy that the pipeline reads and the caller deletes.
    file_path = UPLOAD_DIR / f"{document.id}_{file.filename}"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("wb") as handle:
        async for chunk in blob_store.get_stream(stored.key):
            handle.write(chunk)
    document.classify(classification, retention_days=policy.retention_days)
    if domain:
        document.metadata.domain = domain
    if tags:
        document.metadata.tags = [t.strip() for t in tags.split(",") if t.strip()]

    # Project scope, before the save: `project_id` is what makes the document
    # reachable by the uploader's colleagues, and a document saved without it
    # is personal until someone notices.
    if project_id:
        try:
            requested = uuid.UUID(project_id)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="project_id is not a valid UUID.",
            ) from None
        # Membership is checked rather than trusted: otherwise upload is a way
        # to place a document into a project the uploader cannot read.
        if (
            principal.user_id is None
            or requested not in await get_project_repository().member_project_ids(principal.user_id)
        ):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND, detail="Project not found"
            ) from None
        document.project_id = requested

    document_repo = get_document_repository()
    await document_repo.save(document)

    # Register the revision now, when the uploader's own reading of the title
    # block is available. Doing it here rather than during ingestion means an
    # explicit drawing number is never overruled by what the extractor found,
    # and that a superseded sheet stops being current the moment the new one
    # is accepted rather than minutes later when parsing finishes.
    if drawing_number:
        try:
            await get_register_revision().execute(
                document,
                drawing_number=drawing_number.strip(),
                revision_label=(revision_label or "").strip() or "-",
            )
            await document_repo.update(document)
        except Exception as exc:
            # A register that refuses the upload is worse than a gap in it.
            logger.warning(
                "explicit_revision_registration_failed",
                document_id=str(document.id),
                drawing_number=drawing_number,
                error=str(exc),
            )

    logger.info(
        "document_upload_received",
        doc_id=str(document.id),
        file=file.filename,
        size=stored.size_bytes,
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

    # Enqueued rather than run in this process. Ingestion of a drawing set is
    # minutes of CPU-bound work -- CAD parsing, OCR, layout inference,
    # embedding -- and running it here meant one upload could starve every
    # other request on a single-worker uvicorn, and an API deploy mid-run
    # silently abandoned the document.
    job = await enqueue_ingestion(document.id, file_path)
    logger.info("ingestion_enqueued", doc_id=str(document.id), job_id=str(job.id))

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
    # caller may read and every page is full. Filtering the page afterwards
    # disclosed an accurate count of documents they had no right to know
    # existed, and short pages revealed how many were withheld. Reach includes
    # their projects; a lookup error fails closed to owner-only.
    project_ids: list[uuid.UUID] = []
    if principal.user_id is not None:
        try:
            project_ids = await get_project_repository().member_project_ids(principal.user_id)
        except Exception as exc:
            logger.warning("document_list_project_scope_failed", error=str(exc))

    documents, total = await document_repo.list_by_user(
        principal.user_id,
        page=page,
        size=size,
        sensitivity_in=Sensitivity.values_at_or_below(principal.clearance),
        search=search,
        project_ids=project_ids,
        # Admins see the whole corpus here for the same reason they can open
        # any single document: they already reclassify and delete across it.
        # Without this the two views disagree in the other direction -- an
        # empty list beside a detail endpoint that opens anything by id --
        # which is the same inconsistency, just mirrored. Clearance still
        # applies above: `all_documents` widens reach, never classification.
        all_documents=principal.is_admin,
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
        ) from None
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
        ) from None

    document.classify(new_classification, retention_days=policy.retention_days)
    await get_document_repository().update(document)

    doc_uuid = uuid.UUID(document_id)
    chunks_reclassified = await get_chunk_repository().set_sensitivity(doc_uuid, new_classification)
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
    return {
        "document_id": document_id,
        "deleted_chunks": deleted_chunks,
        "message": "Document deleted.",
    }


async def _load_readable_document(document_id: str, principal: Principal) -> Document:
    """Fetch a document, 404-ing when the principal may not read it.

    Two independent checks, and both must pass:

    **Reach.** The caller owns the document, belongs to its project, or is an
    admin. This used to be missing entirely -- the function checked only
    clearance, so `GET /documents/{id}` handed any document to any
    authenticated caller who knew its id, as did `/chunks`,
    `/intelligence` and `/original`, which returns the original file. The
    list endpoint scopes strictly to `user_id`, so the documents were hidden
    from the UI while remaining fully readable to anyone who had ever seen an
    id -- in a citation, a log line, or a colleague's URL.

    **Clearance.** A project member still cannot read a document classified
    above their clearance. The two dimensions are orthogonal and neither
    substitutes for the other.

    404 rather than 403 on either failure is intentional: a 403 would confirm
    that a document with that id exists, which is itself information the
    caller is not entitled to. The audit entry records the true reason.
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document_id is not a valid UUID.",
        ) from None

    document = await get_document_repository().get_by_id(doc_uuid)
    if document is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Document not found")

    if not await _is_within_reach(document, principal):
        access_denied.labels(reason="document_outside_scope").inc()
        await audit_record(
            action=AuditAction.ACCESS_DENIED,
            actor_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document",
            resource_id=document_id,
            outcome=AuditOutcome.DENIED,
            reason="document belongs to another user and no shared project",
            control_id="C-MAP-01",
        )
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


async def _is_within_reach(document: Document, principal: Principal) -> bool:
    """Whether this caller is entitled to the document at all.

    Ownership or shared project membership. Admins are allowed through
    deliberately: they can already reclassify and delete any document, and
    the admin document views would otherwise show a list they cannot open.
    Every such access is audited under the actor's real role, so the
    exception is visible rather than silent.
    """
    if principal.is_admin:
        return True
    if principal.user_id is not None and document.user_id == principal.user_id:
        return True
    if document.project_id is None or principal.user_id is None:
        return False

    try:
        return document.project_id in await get_project_repository().member_project_ids(
            principal.user_id
        )
    except Exception as exc:
        # Fail closed. A membership lookup that errors must deny, never
        # widen -- the alternative turns a database blip into an access leak.
        logger.warning("project_membership_lookup_failed", error=str(exc))
        return False


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
        error_message=document.error_message,
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


class ReprocessResponse(BaseModel):
    document_id: str
    job_id: str
    status: str
    message: str


@router.post("/documents/{document_id}/reprocess", response_model=ReprocessResponse)
async def reprocess_document(
    document_id: str,
    principal: Principal = Depends(require_role(Role.STEWARD, Role.ADMIN)),
) -> ReprocessResponse:
    """Re-run ingestion for a document.

    The way out of every stuck or failed ingestion, and the way to re-derive
    a corpus after a chunking or embedding change -- previously the only
    route back into the pipeline was to upload the file again, which created
    a second document with a new id and left the first one stuck.

    Idempotent by construction: JobRunner deletes what a previous attempt
    wrote for this document before writing again, so re-running cannot
    double its chunks. A document already being ingested is not enqueued
    twice -- two workers writing the same chunks would leave the loser's
    partial output behind as duplicates.
    """
    # Called for its side effect: 404s if the caller may not read it.
    await _load_readable_document(document_id, principal)
    doc_uuid = uuid.UUID(document_id)

    if await get_job_repository().has_active_job(doc_uuid):
        return ReprocessResponse(
            document_id=document_id,
            job_id="",
            status="already_running",
            message="Ingestion for this document is already in flight.",
        )

    job = await enqueue_ingestion(doc_uuid, None, job_type=JobType.REINDEX_DOCUMENT)
    await audit_record(
        action=AuditAction.DOCUMENT_UPLOADED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="document",
        resource_id=document_id,
        outcome=AuditOutcome.COMPLETED,
        reason="reprocess requested",
    )
    logger.info("document_reprocess_queued", doc_id=document_id, job_id=str(job.id))
    return ReprocessResponse(
        document_id=document_id,
        job_id=str(job.id),
        status=job.status.value,
        message="Reprocessing queued.",
    )


class JobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    error_message: str | None
    created_at: str
    finished_at: str | None
    # Which upload this job is for. Absent on the per-document route, where
    # the document is already the context; present on the queue, where a
    # list of bare ids cannot answer "which of my uploads is stuck".
    document_id: str | None = None
    document_name: str | None = None
    started_at: str | None = None


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    status: list[str] = Query(default=[]),
    limit: int = 50,
    principal: Principal = Depends(get_principal),
) -> list[JobResponse]:
    """The ingestion queue: what is running, what failed, what is waiting.

    Scoped to the caller's own uploads, because a job row carries no
    classification of its own -- the safe scope is the ownership of the
    document it refers to. Admins see the whole queue, matching the document
    list and detail routes; operating the queue is the reason the role
    exists.

    An unknown status is ignored rather than rejected: a queue view polls
    this on a timer, and a 422 from a stale client would replace the queue
    with an error page rather than a slightly wrong filter.
    """
    wanted: list[JobStatus] = []
    for value in status:
        try:
            wanted.append(JobStatus(value))
        except ValueError:
            logger.info("job_status_filter_ignored", value=value)

    rows = await get_job_repository().list_recent(
        statuses=wanted or None,
        limit=limit,
        user_id=principal.user_id,
        all_documents=principal.is_admin,
    )
    return [
        JobResponse(
            id=str(job.id),
            job_type=job.job_type.value,
            status=job.status.value,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error_message=job.error_message,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
            document_id=str(job.document_id) if job.document_id else None,
            document_name=document_name,
        )
        for job, document_name in rows
    ]


@router.get("/documents/{document_id}/jobs", response_model=list[JobResponse])
async def list_document_jobs(
    document_id: str,
    principal: Principal = Depends(get_principal),
) -> list[JobResponse]:
    """What has been attempted for this document, and what became of it.

    The answer to "my upload never finished" -- previously unanswerable,
    because nothing recorded the attempt.
    """
    await _load_readable_document(document_id, principal)
    jobs = await get_job_repository().list_for_document(uuid.UUID(document_id))
    return [
        JobResponse(
            id=str(job.id),
            job_type=job.job_type.value,
            status=job.status.value,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error_message=job.error_message,
            created_at=job.created_at.isoformat(),
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
        )
        for job in jobs
    ]


@router.get("/documents/{document_id}/original")
async def download_original(
    document_id: str,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    """Stream the stored original.

    Proxied through the API rather than handed out as a presigned URL. A
    presigned URL bypasses clearance and project scope entirely -- the store
    has no idea who the caller is -- and for a corpus of client structural
    drawings that is the one thing this system must not do. The cost is that
    bytes pass through the API; the benefit is that every download is an
    authorised download.
    """
    document = await _load_readable_document(document_id, principal)
    if not document.file_path:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="No stored original for this document.",
        ) from None

    blob_store = get_blob_store(get_settings())
    if not await blob_store.exists(document.file_path):
        # The row survived but the blob did not -- the exact symptom of the
        # unmounted-volume bug this storage layer replaced.
        logger.error("document_blob_missing", doc_id=document_id, key=document.file_path)
        raise HTTPException(
            status_code=http_status.HTTP_410_GONE,
            detail="The stored file is no longer available.",
        ) from None

    return StreamingResponse(
        blob_store.get_stream(document.file_path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{document.file_name}"',
            "X-Document-Id": str(document.id),
        },
    )


class ReleaseRequest(BaseModel):
    """Why a human decided a quarantined document belongs in the corpus."""

    reason: str = ""


@router.post("/documents/{document_id}/release", response_model=ReprocessResponse)
async def release_document(
    document_id: str,
    body: ReleaseRequest | None = None,
    principal: Principal = Depends(require_role(Role.STEWARD, Role.ADMIN)),
) -> ReprocessResponse:
    """Send a quarantined document back through normal ingestion.

    Release means "an authorised human reviewed this and wants ingestion to
    proceed". It emphatically does **not** mean "skip the screen from now on":
    the document is put back to PENDING and enqueued on the ordinary ingestion
    job, so it is parsed, screened, classified and chunked exactly as an
    upload is. A file that still fails the policy is quarantined again, and
    that is the correct outcome -- a release that disabled the check would
    turn one reviewer's judgement into a permanent hole in the control.

    Reusing `enqueue_ingestion` rather than writing a shortcut is what
    guarantees that. There is no path here into Qdrant or Elasticsearch that
    does not go through the pipeline.
    """
    # Called for its side effect: 404s if the caller may not read it, which
    # is also what stops this confirming that a restricted document exists.
    document = await _load_readable_document(document_id, principal)
    doc_uuid = uuid.UUID(document_id)

    if document.status is not DocumentStatus.QUARANTINED:
        # 409 rather than 400: the request is well formed and the caller is
        # permitted, but the document is not in a state where release means
        # anything. Saying so is more useful than silently re-ingesting.
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"Document is {document.status.value}, not quarantined. "
                "Use reprocess to re-run ingestion on a document that is not "
                "being held back."
            ),
        )

    if await get_job_repository().has_active_job(doc_uuid):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Ingestion for this document is already in flight.",
        )

    previous = document.status.value
    # Back to PENDING before the job is queued, so the queue and the document
    # never disagree about whether it is waiting.
    document.status = DocumentStatus.PENDING
    document.error_message = None
    await get_document_repository().update(document)

    job = await enqueue_ingestion(doc_uuid, None, job_type=JobType.REINDEX_DOCUMENT)
    await audit_record(
        action=AuditAction.QUARANTINE_RELEASED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="document",
        resource_id=document_id,
        outcome=AuditOutcome.COMPLETED,
        reason=(body.reason if body and body.reason else "released for re-ingestion"),
        before={"status": previous},
        after={"status": DocumentStatus.PENDING.value, "job_id": str(job.id)},
    )
    logger.info(
        "document_quarantine_released",
        doc_id=document_id,
        job_id=str(job.id),
        actor=str(principal.user_id),
        previous_status=previous,
    )
    return ReprocessResponse(
        document_id=document_id,
        job_id=str(job.id),
        status=job.status.value,
        message=(
            "Released. The document re-enters normal ingestion and is screened "
            "again — if it still fails the content policy it will be quarantined."
        ),
    )
