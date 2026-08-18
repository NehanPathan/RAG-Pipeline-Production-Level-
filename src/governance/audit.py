from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import audit_events, policy_violations
from src.monitoring.tracing import get_current_trace_id

logger = get_logger(__name__)


class AuditAction(str, Enum):
    """Every governed action worth reconstructing after the fact.

    Kept as a closed enum rather than free-text so the audit trail can be
    queried and alerted on. Adding a value is a deliberate act.
    """

    SETTING_CHANGED = "setting_changed"
    KILL_SWITCH_TOGGLED = "kill_switch_toggled"
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_DELETED = "document_deleted"
    DOCUMENT_RECLASSIFIED = "document_reclassified"
    ACCESS_DENIED = "access_denied"
    POLICY_VIOLATION = "policy_violation"
    ANSWER_REFUSED = "answer_refused"
    EVAL_RUN_STARTED = "eval_run_started"
    FEEDBACK_SUBMITTED = "feedback_submitted"
    RETENTION_PURGED = "retention_purged"


class AuditOutcome(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"


async def record(
    *,
    action: AuditAction,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    resource_type: str = "",
    resource_id: str | None = None,
    outcome: AuditOutcome = AuditOutcome.COMPLETED,
    before: Any = None,
    after: Any = None,
    reason: str = "",
    control_id: str = "",
) -> None:
    """Append one entry to the audit trail.

    Writes to both the `audit_log` table (queryable, retained, the record of
    account) and the structured log stream (immediately visible in whatever
    tails stdout, and carries the same trace_id as the request that caused
    it, so an audit entry can be expanded into the full trace).

    A failed audit write is logged and counted but does not fail the caller.
    That is a deliberate trade: making every governed action depend on a
    healthy Postgres would convert an audit outage into a full outage. The
    `audit_write_failed` log line plus the `rag_audit_events_total{outcome=
    "write_failed"}` counter is what makes the gap detectable — alert on it.
    """
    trace_id = get_current_trace_id()
    entry = {
        "action": action.value,
        "actor_id": str(actor_id) if actor_id else None,
        "actor_role": actor_role,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome.value,
        "reason": reason,
        "control_id": control_id,
        "trace_id": trace_id,
    }
    logger.info("audit", **entry)

    audit_events.labels(action=action.value, outcome=outcome.value).inc()
    if action is AuditAction.POLICY_VIOLATION:
        policy_violations.labels(control_id=control_id or "unknown").inc()

    try:
        from src.infrastructure.database.postgres.models import AuditLogModel

        session_factory = get_session_factory()
        async with session_factory() as session:
            session.add(
                AuditLogModel(
                    trace_id=trace_id,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    action=action.value,
                    resource_type=resource_type,
                    resource_id=str(resource_id) if resource_id else None,
                    outcome=outcome.value,
                    reason=reason[:2000],
                    control_id=control_id or None,
                    before_state=_serialise(before),
                    after_state=_serialise(after),
                )
            )
            await session.commit()
    except Exception as exc:
        audit_events.labels(action=action.value, outcome="write_failed").inc()
        logger.error("audit_write_failed", action=action.value, error=str(exc))


def _serialise(value: Any) -> dict | None:
    """Coerce arbitrary before/after payloads into a JSONB-safe dict.

    Scalars are wrapped rather than dropped so a settings change from `20`
    to `50` still records both sides.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    return {"value": _coerce(value)}


def _coerce(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    return str(value)
