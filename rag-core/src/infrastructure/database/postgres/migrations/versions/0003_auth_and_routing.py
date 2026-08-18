"""Authentication identity columns on users.

Closes risk R-G03's storage half: the platform can now link a verified
Firebase identity to a local user row, so audit attribution names a real
person rather than the shared development placeholder.

`firebase_uid` is unique so one Firebase account can never map to two local
users, and nullable so the development placeholder user and any locally
created accounts keep working unchanged.

No backfill: existing rows get NULL, which `resolve_principal` treats as "not
linked to an identity provider" — exactly correct for the dev placeholder.

Revision ID: 0003_auth_routing
Revises: 0002_governance
Create Date: 2026-08-06
"""
from __future__ import annotations

from alembic import op

revision = "0003_auth_routing"
down_revision = "0002_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_uid VARCHAR(128)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(255)")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ")

    # Unique rather than a plain index: the constraint is the control. Two
    # local users sharing one Firebase account would mean a single sign-in
    # resolving non-deterministically to different clearances.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_firebase_uid "
        "ON users (firebase_uid) WHERE firebase_uid IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_firebase_uid")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS display_name")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS firebase_uid")
