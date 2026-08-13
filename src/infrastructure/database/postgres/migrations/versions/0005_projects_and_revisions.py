"""Projects, drawings and revisions.

Two domain facts the platform had no way to express:

**Projects.** Steel work is organised by job, and a job is what engineers
share with one another. Isolation was per-`user_id`, so two engineers on the
same project could not see each other's drawings. `project_id` becomes a
second access dimension *alongside* clearance, never a replacement: a
project member still cannot read a `restricted` document above their
clearance. Project membership grants reach; clearance grants depth.

**Revisions.** `S-104` is one drawing; Rev A, Rev B and Rev C are three
documents of it. Without a stable drawing identity there is nothing for "the
current baseplate thickness on S-104" to mean, and the honest answer to a
question about a superseded sheet is not the superseded sheet's answer.

Migration safety: every column is nullable and every existing row keeps
`project_id IS NULL`, which reads as "personal -- visible to its owner and
nobody else". No backfill widens access to anything. `is_latest` defaults
true so pre-existing documents, which have no revision history, are treated
as current.

Revision ID: 0005_projects_revisions
Revises: 0004_chunk_denorm
Create Date: 2026-08-12
"""
from __future__ import annotations

from alembic import op

revision = "0005_projects_revisions"
down_revision = "0004_chunk_denorm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id UUID PRIMARY KEY,
            project_number VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            client_name VARCHAR(255),
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            start_date TIMESTAMPTZ,
            target_completion_date TIMESTAMPTZ,
            default_sensitivity VARCHAR(20),
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_projects_status ON projects (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_members (
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_role VARCHAR(20) NOT NULL DEFAULT 'reader',
            added_by UUID,
            added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, user_id)
        )
        """
    )
    # "Which projects am I in" runs on every retrieval, to build the access
    # scope. The composite primary key indexes the other direction.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_members_user ON project_members (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS drawings (
            id UUID PRIMARY KEY,
            project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
            drawing_number VARCHAR(64) NOT NULL,
            sheet_number VARCHAR(32),
            discipline VARCHAR(32),
            title VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_drawings_identity UNIQUE (project_id, drawing_number, sheet_number)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_drawings_number ON drawings (drawing_number)")

    for column, ddl in (
        ("project_id", "UUID REFERENCES projects(id) ON DELETE SET NULL"),
        ("drawing_id", "UUID REFERENCES drawings(id) ON DELETE SET NULL"),
        ("revision_label", "VARCHAR(16)"),
        ("revision_index", "INTEGER"),
        ("revision_date", "TIMESTAMPTZ"),
        ("revision_note", "TEXT"),
        ("is_latest", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("superseded_by_document_id", "UUID"),
        ("superseded_at", "TIMESTAMPTZ"),
    ):
        op.execute(f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {column} {ddl}")

    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_project ON documents (project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_drawing ON documents (drawing_id)")

    # Exactly one current revision per drawing, enforced by the database
    # rather than by the code that flips the flag. A supersede is two writes,
    # and a partial unique index is what makes a half-applied one impossible
    # instead of merely unlikely.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_drawings_one_latest
        ON documents (drawing_id)
        WHERE drawing_id IS NOT NULL AND is_latest
        """
    )

    for column, ddl in (
        ("project_id", "UUID"),
        ("project_number", "VARCHAR(64)"),
        ("drawing_id", "UUID"),
        ("drawing_number", "VARCHAR(64)"),
        ("revision_label", "VARCHAR(16)"),
        ("is_latest", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ):
        op.execute(f"ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS {column} {ddl}")

    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_project ON document_chunks (project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_drawing ON document_chunks (drawing_id)")


def downgrade() -> None:
    for column in (
        "is_latest",
        "revision_label",
        "drawing_number",
        "drawing_id",
        "project_number",
        "project_id",
    ):
        op.execute(f"ALTER TABLE document_chunks DROP COLUMN IF EXISTS {column}")

    op.execute("DROP INDEX IF EXISTS uq_drawings_one_latest")
    for column in (
        "superseded_at",
        "superseded_by_document_id",
        "is_latest",
        "revision_note",
        "revision_date",
        "revision_index",
        "revision_label",
        "drawing_id",
        "project_id",
    ):
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {column}")

    op.execute("DROP TABLE IF EXISTS drawings")
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP TABLE IF EXISTS projects")
