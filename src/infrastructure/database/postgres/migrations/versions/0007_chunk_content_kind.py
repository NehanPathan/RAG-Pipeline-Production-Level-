"""Record how each chunk's text was obtained.

A measurement read from a DXF is the CAD value. The same measurement on a
plotted PDF is a rendered string, and on a scanned sheet it is OCR's reading
of a rendered string. Retrieval could not previously tell these apart, so an
answer quoting a dimension had no way to say how much to trust it.

`content_kind` is denormalized onto both search backends for filtering, and
kept here because `scripts/reindex_chunks.py` rebuilds those backends from
Postgres -- a payload-only field would be erased by the first reindex.

Revision ID: 0007_chunk_content_kind
Revises: 0006_jobs
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0007_chunk_content_kind"
down_revision = "0006_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS content_kind VARCHAR(32)")
    # Left NULL for existing rows rather than backfilled to 'prose'. The
    # honest value for a chunk ingested before this existed is "unknown", and
    # asserting prose would mislabel every drawing already in the corpus.
    # `scripts/reindex_chunks.py` cannot recover it either -- it needs the
    # source file -- so a re-ingest is what fills these in.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_content_kind "
        "ON document_chunks (content_kind) WHERE content_kind IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_kind")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_kind")
