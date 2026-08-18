from __future__ import annotations

import pytest

from src.tools.base import ToolError
from src.tools.builtin.sql_query import _validate

ALLOWED = ["documents", "evaluation_runs"]


class TestReadOnlyEnforcement:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO documents VALUES (1)",
            "UPDATE documents SET status='x'",
            "DELETE FROM documents",
            "DROP TABLE documents",
            "TRUNCATE documents",
            "ALTER TABLE documents ADD COLUMN x INT",
            "GRANT ALL ON documents TO public",
        ],
    )
    def test_writes_are_rejected(self, sql):
        with pytest.raises(ToolError):
            _validate(sql, ALLOWED, 100)

    def test_stacked_statements_are_rejected(self):
        """The classic injection shape. Even though the read-only transaction
        would also stop it, it must never reach the database."""
        with pytest.raises(ToolError, match="Multiple SQL statements"):
            _validate("SELECT 1 FROM documents; DROP TABLE documents", ALLOWED, 100)

    def test_non_select_leading_keyword_is_rejected(self):
        with pytest.raises(ToolError, match="Only SELECT"):
            _validate("EXPLAIN SELECT * FROM documents", ALLOWED, 100)


class TestTableAllowList:
    def test_allowed_table_passes(self):
        assert "documents" in _validate("SELECT id FROM documents", ALLOWED, 100)

    def test_unlisted_table_is_rejected(self):
        with pytest.raises(ToolError, match="not on the allow-list"):
            _validate("SELECT * FROM users", ALLOWED, 100)

    def test_join_onto_an_unlisted_table_is_rejected(self):
        """Checking only the FROM clause would let a JOIN reach anything."""
        with pytest.raises(ToolError, match="not on the allow-list"):
            _validate(
                "SELECT d.id FROM documents d JOIN users u ON u.id = d.user_id",
                ALLOWED,
                100,
            )

    def test_schema_qualified_names_resolve_to_the_table(self):
        assert _validate("SELECT id FROM public.documents", ALLOWED, 100)

    def test_query_with_no_identifiable_table_is_rejected(self):
        with pytest.raises(ToolError, match="which tables"):
            _validate("SELECT 1", ALLOWED, 100)

    def test_empty_allow_list_permits_nothing(self):
        with pytest.raises(ToolError):
            _validate("SELECT id FROM documents", [], 100)


class TestRowLimiting:
    def test_limit_is_added_when_absent(self):
        assert _validate("SELECT id FROM documents", ALLOWED, 50).endswith("LIMIT 50")

    def test_existing_limit_is_respected(self):
        result = _validate("SELECT id FROM documents LIMIT 5", ALLOWED, 50)
        assert result.endswith("LIMIT 5")
        assert "LIMIT 50" not in result

    def test_trailing_semicolon_is_stripped(self):
        assert ";" not in _validate("SELECT id FROM documents;", ALLOWED, 10)
