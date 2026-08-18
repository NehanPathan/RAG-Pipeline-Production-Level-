"""Baseline schema.

The migration chain could not build a database from empty. Every later
migration uses `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, and
none of them creates the core tables -- `users`, `documents`,
`document_chunks` and the rest. They existed only because
`Base.metadata.create_all` runs at startup when APP_ENV=development.

So `alembic upgrade head` against a fresh database reported success and
created *nothing*, and a production deploy (which skips create_all) would
have come up with an empty schema and no error to explain it. Found by the
integration tests, which are the first thing to run the chain against a real
empty Postgres.

This is a squash, not a historical snapshot. It creates every table as the
models define it today, because the core tables never had migrations and the
later ones now reference them -- `documents` carries a foreign key to
`projects`, so no ordering exists in which a partial baseline works.

Everything downstream is already idempotent, so on a fresh database
migrations 0001-0006 apply cleanly and do nothing, and on an existing one
this baseline does nothing and they apply as before.

Revision ID: 0000_baseline
Revises:
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op

revision = "0000_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE IF NOT EXISTS audit_log (
	id UUID NOT NULL,
	trace_id VARCHAR(64),
	actor_id UUID,
	actor_role VARCHAR(50),
	action VARCHAR(64) NOT NULL,
	resource_type VARCHAR(64),
	resource_id VARCHAR(255),
	outcome VARCHAR(20) NOT NULL,
	reason TEXT,
	control_id VARCHAR(32),
	before_state JSONB,
	after_state JSONB,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS users (
	id UUID NOT NULL,
	email VARCHAR(255) NOT NULL,
	hashed_password VARCHAR(255),
	role VARCHAR(50) NOT NULL,
	is_active BOOLEAN NOT NULL,
	firebase_uid VARCHAR(128),
	display_name VARCHAR(255),
	email_verified BOOLEAN NOT NULL,
	last_login_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (email),
	UNIQUE (firebase_uid)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS api_keys (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	key_hash VARCHAR(255) NOT NULL,
	name VARCHAR(100) NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (key_hash)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS conversations (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	title VARCHAR(500),
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS evaluation_runs (
	id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	dataset_name VARCHAR(255) NOT NULL,
	triggered_by VARCHAR(50) NOT NULL,
	user_id UUID,
	model_config_data JSONB NOT NULL,
	retrieval_config JSONB NOT NULL,
	status VARCHAR(50) NOT NULL,
	total_items INTEGER NOT NULL,
	completed_items INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	error_message TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS projects (
	id UUID NOT NULL,
	project_number VARCHAR(64) NOT NULL,
	name VARCHAR(255) NOT NULL,
	client_name VARCHAR(255),
	status VARCHAR(32) NOT NULL,
	start_date TIMESTAMP WITH TIME ZONE,
	target_completion_date TIMESTAMP WITH TIME ZONE,
	default_sensitivity VARCHAR(20),
	created_by UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	archived_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (project_number),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS system_settings (
	id UUID NOT NULL,
	key VARCHAR(255) NOT NULL,
	value JSONB NOT NULL,
	description TEXT,
	updated_by UUID,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (key),
	FOREIGN KEY(updated_by) REFERENCES users (id)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS drawings (
	id UUID NOT NULL,
	project_id UUID,
	drawing_number VARCHAR(64) NOT NULL,
	sheet_number VARCHAR(32),
	discipline VARCHAR(32),
	title VARCHAR(500),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_drawings_identity UNIQUE (project_id, drawing_number, sheet_number),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS evaluation_metrics (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	metric_name VARCHAR(100) NOT NULL,
	value FLOAT NOT NULL,
	aggregation VARCHAR(20) NOT NULL,
	sample_size INTEGER,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES evaluation_runs (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS messages (
	id UUID NOT NULL,
	conversation_id UUID NOT NULL,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	citations JSONB NOT NULL,
	retrieved_chunks JSONB NOT NULL,
	model_used VARCHAR(100),
	tokens_used JSONB,
	latency_ms INTEGER,
	langfuse_trace_id VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS project_members (
	project_id UUID NOT NULL,
	user_id UUID NOT NULL,
	project_role VARCHAR(20) NOT NULL,
	added_by UUID,
	added_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (project_id, user_id),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS documents (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	file_name VARCHAR(500) NOT NULL,
	file_type VARCHAR(50) NOT NULL,
	file_size_bytes BIGINT NOT NULL,
	file_path VARCHAR(1000),
	status VARCHAR(50) NOT NULL,
	error_message TEXT,
	page_count INTEGER,
	word_count INTEGER,
	loader_used VARCHAR(100),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	indexed_at TIMESTAMP WITH TIME ZONE,
	sensitivity VARCHAR(20),
	retention_until TIMESTAMP WITH TIME ZONE,
	project_id UUID,
	drawing_id UUID,
	revision_label VARCHAR(16),
	revision_index INTEGER,
	revision_date TIMESTAMP WITH TIME ZONE,
	revision_note TEXT,
	is_latest BOOLEAN NOT NULL,
	superseded_by_document_id UUID,
	superseded_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
	FOREIGN KEY(drawing_id) REFERENCES drawings (id) ON DELETE SET NULL
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS online_eval_samples (
	id UUID NOT NULL,
	trace_id VARCHAR(64),
	message_id UUID,
	question TEXT NOT NULL,
	answer TEXT NOT NULL,
	context_count INTEGER NOT NULL,
	citation_count INTEGER NOT NULL,
	scores JSONB NOT NULL,
	scorer_model VARCHAR(100),
	status VARCHAR(20) NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE SET NULL
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS user_feedback (
	id UUID NOT NULL,
	message_id UUID,
	trace_id VARCHAR(64),
	user_id UUID NOT NULL,
	rating SMALLINT NOT NULL,
	comment TEXT,
	feedback_tags VARCHAR[],
	promoted_to_dataset BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id)
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS document_chunks (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	parent_chunk_id UUID,
	chunk_type VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	position INTEGER NOT NULL,
	token_count INTEGER,
	page_number INTEGER,
	section VARCHAR(500),
	contains_table BOOLEAN NOT NULL,
	embedding_model VARCHAR(100),
	qdrant_point_id UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	section_title VARCHAR(500),
	heading_level INTEGER,
	semantic_cluster INTEGER,
	ocr_confidence FLOAT,
	language VARCHAR(20),
	sensitivity VARCHAR(20),
	user_id UUID,
	domain VARCHAR(100),
	tags VARCHAR[],
	file_type VARCHAR(50),
	document_name VARCHAR(500),
	project_id UUID,
	project_number VARCHAR(64),
	drawing_id UUID,
	drawing_number VARCHAR(64),
	revision_label VARCHAR(16),
	is_latest BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE,
	FOREIGN KEY(parent_chunk_id) REFERENCES document_chunks (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS document_intelligence (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	ocr_engine VARCHAR(50) NOT NULL,
	ocr_ran BOOLEAN NOT NULL,
	ocr_confidence_avg FLOAT,
	ocr_processing_time_ms FLOAT NOT NULL,
	ocr_language VARCHAR(20),
	embedding_model_chunking VARCHAR(100) NOT NULL,
	embedding_model_retrieval VARCHAR(100) NOT NULL,
	layout_outline JSONB NOT NULL,
	semantic_graph JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (document_id),
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS document_metadata (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	summary TEXT,
	tags VARCHAR[],
	domain VARCHAR(100),
	language VARCHAR(20) NOT NULL,
	entities JSONB NOT NULL,
	custom_metadata JSONB NOT NULL,
	enriched_at TIMESTAMP WITH TIME ZONE,
	enrichment_model VARCHAR(100),
	PRIMARY KEY (id),
	UNIQUE (document_id),
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
)
        """
    )

    op.execute(
        """
CREATE TABLE IF NOT EXISTS jobs (
	id UUID NOT NULL,
	job_type VARCHAR(50) NOT NULL,
	status VARCHAR(20) NOT NULL,
	document_id UUID,
	attempts INTEGER NOT NULL,
	max_attempts INTEGER NOT NULL,
	error_message TEXT,
	trace_id VARCHAR(64),
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
)
        """
    )


def downgrade() -> None:
    # Dropped in reverse dependency order.
    for table in (
        "jobs",
        "document_metadata",
        "document_intelligence",
        "document_chunks",
        "user_feedback",
        "online_eval_samples",
        "documents",
        "project_members",
        "messages",
        "evaluation_metrics",
        "drawings",
        "system_settings",
        "projects",
        "evaluation_runs",
        "conversations",
        "api_keys",
        "users",
        "audit_log",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
