"""Record where on the page each chunk's text sits.

A citation that resolves to "page 1" of an engineering drawing points at the
whole sheet -- thousands of entities and a dozen callouts. A reader who doubts
the answer has no way to check it, and one who trusts it has no way to find
what it refers to.

`regions` is a JSONB array of rectangles rather than one column each, because
a chunk spans several blocks and the box enclosing all of them would cover the
gaps between as well. `region_precision` says how much to believe them: block
boxes, a parent section's boxes inherited by a token window, or nothing finer
than the page.

Kept in Postgres and not only on the search payloads because
`scripts/reindex_chunks.py` rebuilds those backends from here -- a
payload-only field would be erased by the first reindex.

Revision ID: 0008_chunk_regions
Revises: 0007_chunk_content_kind
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0008_chunk_regions"
down_revision = "0007_chunk_content_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN IF NOT EXISTS regions JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS region_precision VARCHAR(16)")
    # Deliberately no index. Nothing filters or sorts on a highlight
    # rectangle -- these are coordinates a viewer draws, fetched by chunk id
    # along with everything else about the chunk.
    #
    # Existing rows keep an empty array and a NULL precision, which is the
    # truth: the coordinates were never captured and cannot be recovered
    # without the source file. Re-ingesting a document fills them in.


def downgrade() -> None:
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS region_precision")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS regions")
