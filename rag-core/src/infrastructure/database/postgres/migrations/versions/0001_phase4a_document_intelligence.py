"""Phase 4A: additive document_chunks columns + document_intelligence table.

This is the project's first Alembic revision. Every table through Phase 1-3
was bootstrapped via `Base.metadata.create_all()` at API startup
(`src/api/main.py`), which only ever creates missing tables -- it never
alters an existing one. That's harmless for a brand-new `document_chunks`
table (a fresh deploy gets the new columns immediately via create_all), but
it silently no-ops on any database where `document_chunks` already existed
before Phase 4A -- confirmed live in this environment's dev database (a
pre-existing document ingested under Phase 1-3 meant `document_chunks`
already existed, so create_all skipped it entirely and every new-column
insert failed with `UndefinedColumnError`).

Written with `IF NOT EXISTS` guards throughout so it's safe to run against
either a fresh database (where create_all already added everything) or an
existing pre-Phase-4A one (where it's the only thing that actually adds
them) -- see docs/architecture/12_phase4a_design_review.md Module 7 log
entry for the full discovery writeup.

Revision ID: 0001_phase4a
Revises:
Create Date: 2026-07-07
"""
from __future__ import annotations

from alembic import op

revision = "0001_phase4a"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_title VARCHAR(500)")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS heading_level INTEGER")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS semantic_cluster INTEGER")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS ocr_confidence FLOAT")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS language VARCHAR(20)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_intelligence (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
            ocr_engine VARCHAR(50) NOT NULL,
            ocr_ran BOOLEAN NOT NULL DEFAULT FALSE,
            ocr_confidence_avg FLOAT,
            ocr_processing_time_ms FLOAT NOT NULL DEFAULT 0.0,
            ocr_language VARCHAR(20),
            embedding_model_chunking VARCHAR(100) NOT NULL,
            embedding_model_retrieval VARCHAR(100) NOT NULL,
            layout_outline JSONB NOT NULL DEFAULT '{}'::jsonb,
            semantic_graph JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_intelligence")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS language")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS ocr_confidence")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS semantic_cluster")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS heading_level")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS section_title")
