from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy import select

from src.governance import feature_flags
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.policy import get_policy
from src.governance.rbac import Principal, Role, get_principal, require_role
from src.governance.risk_register import get_risk_register
from src.governance.runtime_flags import DEFAULT_FLAGS, get_flags, set_flag
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class FlagUpdate(BaseModel):
    enabled: bool
    reason: str = ""


class AuditEntry(BaseModel):
    id: str
    trace_id: str | None
    actor_id: str | None
    actor_role: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    reason: str | None
    control_id: str | None
    created_at: str


@router.get("/governance/policy")
async def get_active_policy(principal: Principal = Depends(get_principal)) -> dict:
    """The policy actually in force in this process.

    Exposed deliberately: a policy that only exists in a repository can drift
    from the one the running service loaded (a stale container, an
    unapplied config change). This makes the difference observable.
    """
    return {"policy": get_policy().as_dict(), "principal": principal.as_dict()}


@router.get("/governance/risks")
async def get_risks(function: str | None = None) -> dict:
    """The risk register: each risk bound to the metric that watches it.

    `function` filters to one GM3 function (govern / map / measure / manage).
    """
    register = get_risk_register()
    risks = register.by_function(function.lower()) if function else register.risks
    return {
        "version": register.version,
        "count": len(risks),
        "risks": [
            {
                "id": r.id,
                "title": r.title,
                "function": r.function,
                "severity": r.severity,
                "likelihood": r.likelihood,
                "metric": r.metric,
                "threshold": r.threshold,
                "threshold_direction": r.threshold_direction,
                "owner": r.owner,
                "residual_risk": r.residual_risk,
                "controls": [
                    {
                        "id": c.id,
                        "description": c.description,
                        "implemented_in": c.implemented_in,
                    }
                    for c in r.controls
                ],
            }
            for r in risks
        ],
    }


@router.get("/governance/flags")
async def get_runtime_flags() -> dict:
    """Every flag with its value, source and why it exists.

    `source` distinguishes an environment default from an operator override,
    which is the first question during an incident: did someone change this,
    or has it always been off in this environment?
    """
    await get_flags(force_refresh=True)
    return {"flags": feature_flags.describe()}


@router.get("/governance/plugins")
async def list_plugins() -> dict:
    """What is actually loaded, per plugin registry.

    A registry populated by import side effects can silently lose an entry
    when a module fails to import. This endpoint is how that becomes visible
    rather than presenting as "the router never picks that tool".
    """
    from src.governance.pii import detectors as pii_detectors
    from src.llm.registry import providers as llm_providers
    from src.tools.registry import load_builtin_tools
    from src.tools.registry import tools as tool_registry

    load_builtin_tools()
    return {
        "llm_providers": llm_providers.describe(),
        "tools": tool_registry.describe(),
        "pii_detectors": pii_detectors.describe(),
    }


@router.get("/governance/gateway")
async def describe_gateway(
    principal: Principal = Depends(require_role(Role.ADMIN, Role.STEWARD)),
) -> dict:
    """The provider fallback chain in force for each role.

    Privileged: the chain reveals which vendors hold this deployment's data.
    """
    from src.config import get_settings
    from src.llm.registry import build_gateway

    settings = get_settings()
    return {
        role: build_gateway(settings, role).describe() for role in ("small", "large")
    }


@router.put("/governance/flags/{flag_name}")
async def update_runtime_flag(
    flag_name: str,
    body: FlagUpdate,
    principal: Principal = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Flip a kill switch (MANAGE).

    Admin-only and always audited, because this is the most consequential
    control in the system: `answering_enabled=false` stops the product.
    """
    known = set(DEFAULT_FLAGS.as_dict()) | set(feature_flags.FLAGS_BY_NAME)
    if flag_name not in known:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Unknown flag '{flag_name}'. Known: {sorted(known)}.",
        )

    await get_flags(force_refresh=True)
    before = feature_flags.all_flags()
    try:
        await set_flag(flag_name, body.enabled)
    except KeyError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    after = feature_flags.all_flags()

    changed = {k: v for k, v in after.items() if before.get(k) != v}
    await audit_record(
        action=AuditAction.KILL_SWITCH_TOGGLED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="feature_flag",
        resource_id=flag_name,
        outcome=AuditOutcome.COMPLETED,
        before={k: before.get(k) for k in changed},
        after=changed,
        reason=body.reason,
    )
    return {"flags": feature_flags.describe()}


@router.get("/governance/audit", response_model=list[AuditEntry])
async def list_audit_log(
    limit: int = 100,
    action: str | None = None,
    resource_id: str | None = None,
    hours: int = 168,
    principal: Principal = Depends(require_role(Role.ADMIN, Role.STEWARD)),
) -> list[AuditEntry]:
    """Query the audit trail.

    Reading the audit log is itself privileged — it contains who did what to
    which resource, which is exactly the information an attacker would use to
    find the least-watched path.
    """
    from src.infrastructure.database.postgres.models import AuditLogModel

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    stmt = (
        select(AuditLogModel)
        .where(AuditLogModel.created_at >= cutoff)
        .order_by(AuditLogModel.created_at.desc())
        .limit(min(limit, 1000))
    )
    if action:
        stmt = stmt.where(AuditLogModel.action == action)
    if resource_id:
        stmt = stmt.where(AuditLogModel.resource_id == resource_id)

    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()

    return [
        AuditEntry(
            id=str(row.id),
            trace_id=row.trace_id,
            actor_id=str(row.actor_id) if row.actor_id else None,
            actor_role=row.actor_role,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            outcome=row.outcome,
            reason=row.reason,
            control_id=row.control_id,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.post("/governance/retention/run")
async def run_retention(
    dry_run: bool = True,
    principal: Principal = Depends(require_role(Role.ADMIN)),
) -> dict:
    """Purge documents past their retention deadline (MANAGE).

    Defaults to a dry run so the destructive form has to be asked for
    explicitly: `?dry_run=false`. Every purge writes its own audit entry in
    addition to the summary returned here.
    """
    from src.api.dependencies import (
        get_chunk_repository,
        get_document_repository,
        get_query_pipeline,
        get_search_repository,
        get_vector_repository,
    )
    from src.governance.retention import RetentionService

    service = RetentionService(
        document_repo=get_document_repository(),
        chunk_repo=get_chunk_repository(),
        vector_repo=get_vector_repository(),
        search_repo=get_search_repository(),
        query_pipeline=get_query_pipeline(),
    )
    result = await service.purge_expired(dry_run=dry_run)
    logger.info("retention_run_requested", actor=str(principal.user_id), **result.as_dict())
    return result.as_dict()


@router.get("/governance/status")
async def governance_status() -> dict:
    """One-shot readiness view of the GM3 controls.

    Intended for a dashboard tile and for a reviewer who wants to know, in
    one request, whether the governance machinery is actually switched on
    rather than merely present in the codebase.
    """
    policy = get_policy()
    flags = await get_flags()

    # The register is a file on disk, so it can legitimately be absent in a
    # trimmed deployment. Report that as a named condition rather than letting
    # a FileNotFoundError surface as an opaque 500 -- the whole point of this
    # endpoint is telling an operator what is and isn't switched on.
    try:
        register = get_risk_register()
        register_version = register.version
        risks_by_function = {
            fn: len(register.by_function(fn))
            for fn in ("govern", "map", "measure", "manage")
        }
        controls = len(register.control_ids)
    except FileNotFoundError as exc:
        logger.error("risk_register_unavailable", error=str(exc))
        register_version = "unavailable"
        risks_by_function = {}
        controls = 0

    return {
        "policy_version": policy.version,
        "enforcement_mode": policy.enforcement_mode.value,
        "risk_register_version": register_version,
        "risks_by_function": risks_by_function,
        "controls": controls,
        "flags": flags.as_dict(),
        "quality_floors": {
            "faithfulness": policy.min_faithfulness,
            "answer_relevancy": policy.min_answer_relevancy,
            "context_relevancy": policy.min_context_relevancy,
        },
    }
