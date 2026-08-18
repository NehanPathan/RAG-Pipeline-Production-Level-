from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import retention_purged

logger = get_logger(__name__)


@dataclass
class RetentionResult:
    scanned: int
    purged: int
    failed: int
    dry_run: bool
    document_ids: list[str]

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "purged": self.purged,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "document_ids": self.document_ids,
        }


class RetentionService:
    """Deletes documents whose retention deadline has passed (MANAGE).

    Makes `documents.retention_until` load-bearing rather than decorative: a
    retention policy nothing enforces is a statement of intent, and the gap
    between the stated policy and the actual data is precisely what a
    governance review looks for.

    Purges across all four stores — Postgres, Qdrant, Elasticsearch and the
    semantic cache — because a document that survives in any one of them is
    still reachable by a user.
    """

    def __init__(self, document_repo, chunk_repo, vector_repo, search_repo, query_pipeline) -> None:
        self._documents = document_repo
        self._chunks = chunk_repo
        self._vectors = vector_repo
        self._search = search_repo
        self._pipeline = query_pipeline

    async def find_expired(self, now: datetime | None = None) -> list[uuid.UUID]:
        from src.infrastructure.database.postgres.models import DocumentModel

        cutoff = now or datetime.now(timezone.utc)
        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(DocumentModel.id).where(
                    DocumentModel.retention_until.is_not(None),
                    DocumentModel.retention_until <= cutoff,
                )
            )
            return [row[0] for row in result.all()]

    async def purge_expired(self, dry_run: bool = True) -> RetentionResult:
        """Purge every expired document.

        Defaults to `dry_run=True`. Irreversible bulk deletion driven by a
        date column is exactly the kind of job that should require someone to
        opt in explicitly after reading what it intends to remove.
        """
        expired = await self.find_expired()
        purged = 0
        failed = 0

        for document_id in expired:
            if dry_run:
                continue
            try:
                await self._vectors.delete_by_document(document_id)
                await self._search.delete_by_document(document_id)
                await self._pipeline.invalidate_cached_document(document_id)
                await self._documents.delete(document_id)
                purged += 1
                retention_purged.labels(outcome="purged").inc()
                await audit_record(
                    action=AuditAction.RETENTION_PURGED,
                    resource_type="document",
                    resource_id=str(document_id),
                    outcome=AuditOutcome.COMPLETED,
                    reason="retention_until elapsed",
                    control_id="C-MAN-03",
                )
            except Exception as exc:
                failed += 1
                retention_purged.labels(outcome="failed").inc()
                logger.error(
                    "retention_purge_failed", document_id=str(document_id), error=str(exc)
                )

        result = RetentionResult(
            scanned=len(expired),
            purged=purged,
            failed=failed,
            dry_run=dry_run,
            document_ids=[str(d) for d in expired],
        )
        logger.info("retention_run_complete", **result.as_dict())
        return result
