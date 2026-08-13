"""Persisted background jobs.

Ingestion ran in a FastAPI BackgroundTask -- in-process, no retry, no
persistence, dying with the worker. CAD conversion and OCR run for minutes,
so an API restart mid-ingestion left a document in `processing` forever,
with nothing to retry it and nothing recording why it stopped.

This table is what makes "what happened to my upload" a query rather than a
guess, and what lets a stuck-document gauge exist at all.

Revision ID: 0006_jobs
Revises: 0005_projects_revisions
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op

revision = "0006_jobs"
down_revision = "0005_projects_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id UUID PRIMARY KEY,
            job_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            error_message TEXT,
            trace_id VARCHAR(64),
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
        """
    )
    # "What is queued or running" is the query the stuck-document gauge and
    # the worker health check both run.
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_document ON jobs (document_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jobs")
