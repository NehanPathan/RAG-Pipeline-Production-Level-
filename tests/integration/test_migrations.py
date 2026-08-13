"""The migration chain, applied to a real Postgres.

The application calls `Base.metadata.create_all` in development, so the ORM
models and the Alembic chain can drift apart indefinitely and nothing
notices until a production deploy runs the migrations for real. This is the
only thing that catches that.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _columns(session_factory, table: str) -> set[str]:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :table"
            ),
            {"table": table},
        )
        return {row[0] for row in result}


class TestChainApplies:
    async def test_every_table_exists_after_upgrade(self, session_factory):
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
            tables = {row[0] for row in result}

        assert {
            "users",
            "documents",
            "document_chunks",
            "projects",
            "project_members",
            "drawings",
            "jobs",
        } <= tables


class TestDenormalizedColumns:
    async def test_chunks_can_reconstruct_their_search_payload(self, session_factory):
        """Migration 0004's reason for existing: without these columns
        Postgres cannot rebuild a chunk, so any re-index from Postgres wipes
        the tenant filter out of Elasticsearch."""
        columns = await _columns(session_factory, "document_chunks")

        assert {"user_id", "domain", "tags", "file_type", "document_name"} <= columns

    async def test_chunks_carry_project_and_revision_identity(self, session_factory):
        columns = await _columns(session_factory, "document_chunks")

        assert {
            "project_id",
            "project_number",
            "drawing_id",
            "drawing_number",
            "revision_label",
            "is_latest",
        } <= columns


class TestRevisionConstraint:
    async def test_only_one_current_revision_per_drawing(self, session_factory):
        """Enforced by the database, not by the code that flips the flag: a
        supersede is two writes, and a partial unique index makes a
        half-applied one impossible rather than merely unlikely."""
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_drawings_one_latest'")
            )
            definition = result.scalar_one_or_none()

        assert definition is not None, "the partial unique index should exist"
        assert "UNIQUE" in definition.upper()
        assert "is_latest" in definition

    async def test_the_constraint_actually_rejects_a_second_current_revision(
        self, session_factory
    ):
        import uuid

        user_id, drawing_id = uuid.uuid4(), uuid.uuid4()
        async with session_factory() as session:
            await session.execute(
                # `email_verified` is NOT NULL with only a Python-side
                # default, so raw SQL has to supply it.
                text(
                    "INSERT INTO users (id, email, role, is_active, email_verified) "
                    "VALUES (:id, :email, 'admin', true, false)"
                ),
                {"id": user_id, "email": f"{user_id}@test.local"},
            )
            await session.execute(
                text(
                    "INSERT INTO drawings (id, drawing_number) VALUES (:id, 'S-104')"
                ),
                {"id": drawing_id},
            )
            insert = text(
                "INSERT INTO documents "
                "(id, user_id, file_name, file_type, file_size_bytes, status, "
                " drawing_id, is_latest) "
                "VALUES (:id, :user_id, 'S-104.pdf', 'pdf', 1, 'indexed', "
                "        :drawing_id, true)"
            )
            await session.execute(
                insert, {"id": uuid.uuid4(), "user_id": user_id, "drawing_id": drawing_id}
            )

            # The index rejects the write itself, not the commit -- which is
            # the stronger guarantee: a second current revision is never even
            # momentarily visible to a concurrent reader.
            with pytest.raises(Exception) as exc:
                await session.execute(
                    insert,
                    {"id": uuid.uuid4(), "user_id": user_id, "drawing_id": drawing_id},
                )

        assert "uq_drawings_one_latest" in str(exc.value)
