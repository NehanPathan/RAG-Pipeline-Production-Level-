from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.postgres.connection import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Identity-provider link (src/auth/). Nullable so locally-created users
    # and the dev placeholder keep working; unique so one Firebase account
    # can never map to two local users.
    firebase_uid: Mapped[str | None] = mapped_column(String(128), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    api_keys: Mapped[list[APIKeyModel]] = relationship("APIKeyModel", back_populates="user")
    documents: Mapped[list[DocumentModel]] = relationship("DocumentModel", back_populates="user")


class APIKeyModel(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[UserModel] = relationship("UserModel", back_populates="api_keys")




class JobModel(Base):
    """A unit of background work and what became of it.

    Ingestion used to run in a FastAPI BackgroundTask: in-process, no retry,
    no persistence, dying with the worker. A document whose ingestion was
    interrupted sat in `processing` forever with nothing to retry it and
    nothing recording why. This row exists so "what happened to my upload" is
    a query rather than a guess.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Carried from the request that enqueued the job, so a failure minutes or
    # days later still correlates to the upload that caused it.
    trace_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # "What is queued or running right now" is the query the worker
        # health check and the stuck-document gauge both run.
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_document", "document_id"),
    )


class ProjectModel(Base):
    """A job.

    Steel work is organised by project, and a project is the unit engineers
    share with one another -- which is why it is an access scope and not
    merely a label.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_completion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Uploads into this project take this classification unless told
    # otherwise, so a project handling restricted work does not depend on
    # every uploader remembering to say so.
    default_sensitivity: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list[ProjectMemberModel]] = relationship(
        "ProjectMemberModel", back_populates="project", passive_deletes=True
    )

    __table_args__ = (Index("ix_projects_status", "status"),)


class ProjectMemberModel(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_role: Mapped[str] = mapped_column(String(20), nullable=False, default="reader")
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[ProjectModel] = relationship("ProjectModel", back_populates="members")

    # Which projects a user belongs to is looked up on every retrieval, to
    # build the access scope. That is the hot direction here, not the
    # membership list of a given project.
    __table_args__ = (Index("ix_project_members_user", "user_id"),)


class DrawingModel(Base):
    """The stable identity of a drawing across its revisions.

    S-104 is one drawing; Rev A, Rev B and Rev C are three documents. Without
    this row there is nothing for "the current version of S-104" to mean.
    """

    __tablename__ = "drawings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    drawing_number: Mapped[str] = mapped_column(String(64), nullable=False)
    sheet_number: Mapped[str | None] = mapped_column(String(32))
    discipline: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id", "drawing_number", "sheet_number", name="uq_drawings_identity"
        ),
        Index("ix_drawings_number", "drawing_number"),
    )


class DocumentModel(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    loader_used: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Governance MAP columns (see docs/governance/GM3_FRAMEWORK.md). Both are
    # nullable so the migration needs no backfill; NULL `sensitivity` is read
    # as the policy's default_sensitivity, which is `internal` — an
    # unclassified document must not be treated as public.
    sensitivity: Mapped[str | None] = mapped_column(String(20))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Project scope. NULL means "personal": visible to documents.user_id and
    # to nobody else. Existing rows migrate as NULL, so the migration cannot
    # widen access to anything.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL")
    )

    # Revision identity. A `drawings` row is the stable thing -- S-104 -- and
    # each upload of it is one revision. All nullable: a specification or a
    # calculation sheet is a document without being a revision of a drawing.
    drawing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drawings.id", ondelete="SET NULL")
    )
    revision_label: Mapped[str | None] = mapped_column(String(16))
    # Sort key. Labels are inconsistent across practices (A/B/C, 0/1/2,
    # P1/C1), so ordering uses an integer derived at registration rather than
    # comparing label text.
    revision_index: Mapped[int | None] = mapped_column(Integer)
    revision_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision_note: Mapped[str | None] = mapped_column(Text)
    # The current revision of its drawing. Search defaults to these: answering
    # from a superseded sheet is worse than not answering.
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_by_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[UserModel] = relationship("UserModel", back_populates="documents")
    # passive_deletes=True: trust the FK's ondelete="CASCADE" in the DB rather
    # than having the ORM issue `UPDATE ... SET document_id = NULL` first,
    # which violates document_chunks.document_id's NOT NULL constraint.
    metadata_record: Mapped[DocumentMetadataModel | None] = relationship(
        "DocumentMetadataModel", back_populates="document", uselist=False, passive_deletes=True
    )
    chunks: Mapped[list[DocumentChunkModel]] = relationship(
        "DocumentChunkModel", back_populates="document", passive_deletes=True
    )


class DocumentMetadataModel(Base):
    __tablename__ = "document_metadata"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    domain: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(20), default="en")
    entities: Mapped[dict] = mapped_column(JSONB, default=list)
    custom_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_model: Mapped[str | None] = mapped_column(String(100))

    document: Mapped[DocumentModel] = relationship("DocumentModel", back_populates="metadata_record")


class DocumentChunkModel(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="CASCADE"))
    chunk_type: Mapped[str] = mapped_column(String(20), nullable=False, default="child")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    contains_table: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Additive Phase 4A columns (Part 5) -- all nullable, no backfill needed;
    # existing rows read as NULL. See docs/architecture/12_phase4a_design_review.md.
    section_title: Mapped[str | None] = mapped_column(String(500))
    heading_level: Mapped[int | None] = mapped_column(Integer)
    semantic_cluster: Mapped[int | None] = mapped_column(Integer)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(20))

    # Denormalized from documents.sensitivity so a chunk-level read (and the
    # Qdrant/Elasticsearch payloads built from it) carries its classification
    # without a join. Kept in sync by the ingestion pipeline; a
    # reclassification partial-updates both stores (see documents route).
    sensitivity: Mapped[str | None] = mapped_column(String(20))

    # The remaining denormalized fields, given a system of record here.
    #
    # These are written into the Qdrant payload and the Elasticsearch
    # document body at ingest, and `user_id` is the tenant filter every
    # retrieval runs against. Until now they existed *only* in those two
    # stores, which made Postgres unable to reconstruct a chunk faithfully --
    # so any code path that reloaded chunks and re-indexed silently wiped the
    # tenant filter, and no backfill or re-index tool was possible at all.
    # See migration 0004 and scripts/reindex_chunks.py.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    domain: Mapped[str | None] = mapped_column(String(100))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    file_type: Mapped[str | None] = mapped_column(String(50))
    document_name: Mapped[str | None] = mapped_column(String(500))

    # Project and revision identity, denormalized so retrieval filters inside
    # Qdrant/Elasticsearch without a join back to `documents`. `is_latest` in
    # particular sits on the hot path of every query.
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project_number: Mapped[str | None] = mapped_column(String(64))
    drawing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    drawing_number: Mapped[str | None] = mapped_column(String(64))
    revision_label: Mapped[str | None] = mapped_column(String(16))
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # How the text was obtained -- see ContentKind. A column rather than a
    # payload-only field because `scripts/reindex_chunks.py` rebuilds both
    # search backends from Postgres, and anything without a system of record
    # here is silently erased by a reindex.
    content_kind: Mapped[str | None] = mapped_column(String(32))

    document: Mapped[DocumentModel] = relationship("DocumentModel", back_populates="chunks")


class DocumentIntelligenceModel(Base):
    """Document-level OCR/layout/embedding summary (Part 7's Document
    Intelligence UI reads this) -- one row per document, additive new table,
    see docs/architecture/12_phase4a_design_review.md."""

    __tablename__ = "document_intelligence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), unique=True
    )
    ocr_engine: Mapped[str] = mapped_column(String(50), nullable=False)
    ocr_ran: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_confidence_avg: Mapped[float | None] = mapped_column(Float)
    ocr_processing_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_language: Mapped[str | None] = mapped_column(String(20))
    embedding_model_chunking: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model_retrieval: Mapped[str] = mapped_column(String(100), nullable=False)
    layout_outline: Mapped[dict] = mapped_column(JSONB, default=dict)
    semantic_graph: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages: Mapped[list[MessageModel]] = relationship("MessageModel", back_populates="conversation")


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[dict] = mapped_column(JSONB, default=list)
    retrieved_chunks: Mapped[dict] = mapped_column(JSONB, default=list)
    model_used: Mapped[str | None] = mapped_column(String(100))
    tokens_used: Mapped[dict | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[ConversationModel] = relationship("ConversationModel", back_populates="messages")


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(50), default="manual")
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    model_config_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retrieval_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="running")
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    metrics: Mapped[list[EvaluationMetricModel]] = relationship("EvaluationMetricModel", back_populates="run")


class EvaluationMetricModel(Base):
    __tablename__ = "evaluation_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    aggregation: Mapped[str] = mapped_column(String(20), default="mean")
    sample_size: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[EvaluationRunModel] = relationship("EvaluationRunModel", back_populates="metrics")


class UserFeedbackModel(Base):
    __tablename__ = "user_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: a user can rate an answer by quoting the trace id from the
    # response header even when the message row was never written (e.g. the
    # stream was aborted mid-flight). Losing the feedback because the join
    # target is missing would be the wrong trade.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE")
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    feedback_tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    # Set when a negative rating has been promoted into the golden dataset,
    # so the MANAGE feedback loop never enqueues the same complaint twice.
    promoted_to_dataset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_user_feedback_created_at", "created_at"),)


class SystemSettingModel(Base):
    __tablename__ = "system_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AuditLogModel(Base):
    """Append-only record of every governed action (GOVERN function).

    Deliberately has no `updated_at` and no ORM-side update path: an audit
    trail that can be edited is not an audit trail. `actor_id` is nullable
    and carries no foreign key on purpose — deleting a user must never
    cascade away the record of what they did, and system-initiated actions
    (the retention job, startup reclassification) have no actor at all.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_role: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    reason: Mapped[str | None] = mapped_column(Text)
    control_id: Mapped[str | None] = mapped_column(String(32))
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The two queries this table actually serves: "what happened around
        # time T" and "everything that ever touched resource X".
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_resource", "resource_type", "resource_id"),
        Index("ix_audit_log_action", "action"),
    )


class OnlineEvalSampleModel(Base):
    """One live answer pulled for judge scoring (MEASURE function).

    Separate from `evaluation_metrics` because the two answer different
    questions: that table holds aggregates over a fixed golden set, while
    this one holds individual production interactions with their scores
    attached, which is what makes drift on *real* traffic visible.
    """

    __tablename__ = "online_eval_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    context_count: Mapped[int] = mapped_column(Integer, default=0)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    scorer_model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="scored")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_online_eval_created_at", "created_at"),)
