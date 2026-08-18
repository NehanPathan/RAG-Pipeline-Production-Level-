"""Record which CAD layers each chunk's content came from.

On a structural drawing the layer *is* the semantics -- `S-BOLTS`,
`S-SECT_STEEL`, `S-DIMS` -- so "what is on the bolts layer" is a structural
question with an exact answer, not a question about meaning. Without a
filterable field it was served by embedding similarity, where the layer
inventory competed against every other chunk in the corpus on wording alone.

Stored here as well as on both search payloads because
`scripts/reindex_chunks.py` rebuilds those backends from Postgres -- a
payload-only field would be erased by the first reindex.

An array column rather than a scalar: a chunk can legitimately belong to
several layers. An annotation names both the layer its text sits on and the
layer of the geometry its leader points at, and a question about either
should reach it.

Revision ID: 0009_chunk_layers
Revises: 0008_chunk_regions
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0009_chunk_layers"
down_revision = "0008_chunk_regions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN IF NOT EXISTS layers TEXT[] NOT NULL DEFAULT '{}'::text[]"
    )
    # GIN, because every query against this is containment ("is S-BOLTS among
    # this chunk's layers"), which a btree cannot serve. Partial, because the
    # overwhelming majority of chunks are prose and have no layers at all --
    # indexing those empty arrays would double the index for no lookups.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_layers ON document_chunks "
        "USING GIN (layers) WHERE cardinality(layers) > 0"
    )
    # Existing rows keep an empty array, which is the truth: the layer was
    # never captured. Re-ingesting a drawing fills it in.


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_layers")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS layers")
