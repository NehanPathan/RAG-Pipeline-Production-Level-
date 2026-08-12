"""Give the denormalized chunk fields a system of record.

`user_id`, `domain`, `tags`, `file_type` and `document_name` are written
into the Qdrant payload and the Elasticsearch document body at ingest, but
had no columns on `document_chunks`. Postgres therefore could not
reconstruct a chunk faithfully, with two consequences:

1. Any code path that reloaded chunks from Postgres and re-indexed a search
   backend wrote those fields back as null. `PUT /documents/{id}/classification`
   did exactly that, so reclassifying a document removed it from its own
   owner's BM25 filter -- it became unfindable by keyword search for the
   person who uploaded it, with no error anywhere.
2. No re-index or backfill tool was possible, because the source of truth
   for a third of each payload was the thing being rebuilt.

The backfill is idempotent (`WHERE ... IS NULL`) and derives every value
from an existing join, so it changes no access decision -- it only writes
down what the search backends were already using.

Revision ID: 0004_chunk_denorm
Revises: 0003_auth_routing
Create Date: 2026-08-12
"""
from __future__ import annotations

from alembic import op

revision = "0004_chunk_denorm"
down_revision = "0003_auth_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS domain VARCHAR(100)")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tags VARCHAR[]")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS file_type VARCHAR(50)")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS document_name VARCHAR(500)")

    # Backfill from the parent document. Idempotent, and re-runnable: a
    # partially-migrated table is completed rather than double-written.
    op.execute(
        """
        UPDATE document_chunks c
           SET user_id = d.user_id,
               file_type = d.file_type,
               document_name = d.file_name
          FROM documents d
         WHERE d.id = c.document_id
           AND c.user_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE document_chunks c
           SET domain = m.domain,
               tags = m.tags
          FROM document_metadata m
         WHERE m.document_id = c.document_id
           AND c.domain IS NULL
        """
    )

    # The tenant filter runs on every retrieval that touches Postgres chunks;
    # the re-index tool scans by document.
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_user_id ON document_chunks (user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_user_id")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS document_name")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS file_type")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tags")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS domain")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS user_id")
