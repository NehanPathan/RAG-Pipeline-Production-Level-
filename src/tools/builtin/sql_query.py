from __future__ import annotations

import re

from sqlalchemy import text

from src.config import get_settings
from src.governance.rbac import Role
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger
from src.tools.base import Tool, ToolError, ToolRequest, ToolResult
from src.tools.registry import tools

logger = get_logger(__name__)

# Statements that must never appear. This list is *defence in depth*, not the
# control: the actual guarantee comes from the read-only transaction below,
# because keyword filtering on SQL is famously bypassable and anyone relying
# on it alone eventually gets burned.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|grant|revoke|copy|"
    r"vacuum|reindex|call|do|merge|set|commit|rollback|savepoint)\b",
    re.IGNORECASE,
)

_STARTS_READONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", re.IGNORECASE)


class SQLQueryTool(Tool):
    """Read-only SQL against an explicit table allow-list.

    Ships **disabled** (`enable_sql_tool=false`). A tool that turns model
    output into database queries is the single largest blast-radius increase
    available to a prompt injection, so enabling it should be a decision
    someone makes, not a default they inherit.

    Four independent controls, because any one of them can be wrong:

      1. Role gate — analyst or above; the tool sees the principal itself
         rather than trusting the router to have checked.
      2. Statement shape — must begin SELECT/WITH, single statement only.
      3. Table allow-list — an empty allow-list means the tool is unusable,
         which is the correct default. Never "all tables".
      4. Read-only transaction — the actual guarantee. Postgres rejects any
         write inside `SET TRANSACTION READ ONLY` regardless of what slipped
         past the text checks above.
    """

    name = "sql_query"
    description = (
        "Run a read-only SQL SELECT against approved analytics tables to "
        "answer questions about counts, aggregates or structured records. "
        "Use only for questions about structured data, never for document content."
    )
    requires_flag = "enable_sql_tool"
    is_external = False

    async def execute(self, request: ToolRequest) -> ToolResult:
        settings = get_settings()
        allowed = settings.sql_tool_allowed_tables_list

        if not allowed:
            raise ToolError(
                "No tables are allow-listed for SQL access "
                "(SQL_TOOL_ALLOWED_TABLES is empty), so this tool is unusable by design."
            )

        principal = request.principal
        if principal is None or principal.role not in {
            Role.ANALYST.value,
            Role.STEWARD.value,
            Role.ADMIN.value,
        }:
            raise ToolError("SQL access requires the analyst role or above.")

        sql = str(request.args.get("sql") or "").strip()
        if not sql:
            raise ToolError(
                "No SQL statement was supplied. This tool executes SQL; it does "
                "not translate natural language into it."
            )

        statement = _validate(sql, allowed, settings.sql_tool_max_rows)

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                # The real control. Any write attempt raises here even if the
                # text-level checks above were somehow satisfied.
                await session.execute(text("SET TRANSACTION READ ONLY"))
                result = await session.execute(text(statement))
                columns = list(result.keys())
                rows = [list(row) for row in result.fetchall()]
        except Exception as exc:
            logger.warning("sql_tool_execution_failed", error=str(exc))
            raise ToolError(f"Query failed: {exc}") from exc

        logger.info(
            "sql_tool_query_executed",
            actor=str(principal.user_id),
            rows=len(rows),
            columns=len(columns),
        )
        return ToolResult(
            answer=_render(columns, rows),
            data={"columns": columns, "rows": rows, "row_count": len(rows)},
            # Underlying data changes; a cached row count is a wrong row count.
            cacheable=False,
        )


def _validate(sql: str, allowed_tables: list[str], max_rows: int) -> str:
    """Return a safe, row-limited statement or raise."""
    cleaned = sql.strip().rstrip(";").strip()

    if ";" in cleaned:
        # Blocks the classic "SELECT 1; DROP TABLE x" stacked-statement form.
        raise ToolError("Multiple SQL statements are not allowed.")
    if not _STARTS_READONLY.match(cleaned):
        raise ToolError("Only SELECT (or WITH ... SELECT) statements are allowed.")
    if _FORBIDDEN.search(cleaned):
        raise ToolError("The statement contains a keyword that is not permitted.")

    referenced = {t.lower().split(".")[-1] for t in _TABLE_REF.findall(cleaned)}
    permitted = {t.lower() for t in allowed_tables}
    unauthorised = referenced - permitted
    if unauthorised:
        raise ToolError(
            f"Tables not on the allow-list: {sorted(unauthorised)}. "
            f"Permitted: {sorted(permitted)}."
        )
    if not referenced:
        raise ToolError("Could not determine which tables the query reads.")

    if not re.search(r"\blimit\s+\d+", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT {max_rows}"
    return cleaned


def _render(columns: list[str], rows: list[list]) -> str:
    if not rows:
        return "The query returned no rows."
    header = " | ".join(columns)
    divider = "-" * len(header)
    body = "\n".join(" | ".join("" if v is None else str(v) for v in row) for row in rows[:50])
    suffix = f"\n… {len(rows) - 50} more row(s)" if len(rows) > 50 else ""
    return f"{header}\n{divider}\n{body}{suffix}"


@tools.register(
    "sql_query",
    description=SQLQueryTool.description,
    requires_flag=SQLQueryTool.requires_flag,
    tags=("database", "readonly"),
)
def _build_sql_tool() -> Tool:
    return SQLQueryTool()
