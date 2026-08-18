"""Governance (GM3): data classification, audit trail, online evaluation.

Adds the storage the NIST AI RMF functions need:

  MAP     documents.sensitivity / retention_until, document_chunks.sensitivity
  GOVERN  audit_log -- append-only record of every governed action
  MEASURE online_eval_samples -- judged samples of live traffic
          user_feedback.trace_id / promoted_to_dataset, message_id relaxed

Written with `IF NOT EXISTS` guards throughout, following 0001's precedent and
for the same reason: `Base.metadata.create_all()` runs at startup in dev and
creates missing *tables* but never alters existing ones, so this migration
must be safe both on a fresh database (where create_all already added
everything) and on an existing one (where it is the only thing that does).

All new columns are nullable with no backfill. A NULL `sensitivity` is read
as the policy default (`internal`) by Sensitivity.parse rather than as
`public` -- pre-existing documents fail closed, so this migration cannot
silently widen access to data that was already indexed.

Revision ID: 0002_governance
Revises: 0001_phase4a
Create Date: 2026-08-06
"""
from __future__ import annotations

from alembic import op

revision = "0002_governance"
down_revision = "0001_phase4a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- MAP
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20)")

    # Retrieval filters on classification for every query, and the retention
    # job scans on the deadline.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_documents_sensitivity ON documents (sensitivity)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_documents_retention_until "
        "ON documents (retention_until) WHERE retention_until IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_sensitivity "
        "ON document_chunks (sensitivity)"
    )

    # ------------------------------------------------------------- GOVERN
    # No FK on actor_id: deleting a user must never cascade away the record
    # of what they did, and system-initiated actions have no actor at all.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id UUID PRIMARY KEY,
            trace_id VARCHAR(64),
            actor_id UUID,
            actor_role VARCHAR(50),
            action VARCHAR(64) NOT NULL,
            resource_type VARCHAR(64),
            resource_id VARCHAR(255),
            outcome VARCHAR(20) NOT NULL DEFAULT 'completed',
            reason TEXT,
            control_id VARCHAR(32),
            before_state JSONB,
            after_state JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log (created_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_resource ON audit_log (resource_type, resource_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_action ON audit_log (action)")

    # ------------------------------------------------------------ MEASURE
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS online_eval_samples (
            id UUID PRIMARY KEY,
            trace_id VARCHAR(64),
            message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            context_count INTEGER NOT NULL DEFAULT 0,
            citation_count INTEGER NOT NULL DEFAULT 0,
            scores JSONB NOT NULL DEFAULT '{}'::jsonb,
            scorer_model VARCHAR(100),
            status VARCHAR(20) NOT NULL DEFAULT 'scored',
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_online_eval_created_at "
        "ON online_eval_samples (created_at)"
    )

    op.execute("ALTER TABLE user_feedback ADD COLUMN IF NOT EXISTS trace_id VARCHAR(64)")
    op.execute(
        "ALTER TABLE user_feedback ADD COLUMN IF NOT EXISTS "
        "promoted_to_dataset BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Feedback can now be submitted against a trace id alone, for the case
    # where the stream was aborted before the message row was written.
    # Losing the feedback because the join target is missing is the worse
    # trade, so the FK becomes optional.
    op.execute("ALTER TABLE user_feedback ALTER COLUMN message_id DROP NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_feedback_created_at ON user_feedback (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_feedback_created_at")
    op.execute("ALTER TABLE user_feedback DROP COLUMN IF EXISTS promoted_to_dataset")
    op.execute("ALTER TABLE user_feedback DROP COLUMN IF EXISTS trace_id")
    # message_id's NOT NULL is deliberately not restored: rows written since
    # the upgrade may legitimately have a NULL there, and re-adding the
    # constraint would fail against real data.

    op.execute("DROP INDEX IF EXISTS ix_online_eval_created_at")
    op.execute("DROP TABLE IF EXISTS online_eval_samples")

    op.execute("DROP INDEX IF EXISTS ix_audit_log_action")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_resource")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_created_at")
    op.execute("DROP TABLE IF EXISTS audit_log")

    op.execute("DROP INDEX IF EXISTS ix_document_chunks_sensitivity")
    op.execute("DROP INDEX IF EXISTS ix_documents_retention_until")
    op.execute("DROP INDEX IF EXISTS ix_documents_sensitivity")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS sensitivity")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS retention_until")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS sensitivity")
