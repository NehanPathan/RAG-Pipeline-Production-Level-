from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.rbac import Principal, Role, require_role
from src.governance.runtime_flags import SETTINGS_KEY as FLAGS_KEY
from src.infrastructure.database.postgres.connection import get_session_factory
from src.infrastructure.database.postgres.models import SystemSettingModel
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class SettingValue(BaseModel):
    value: dict | str | int | float | bool
    reason: str = ""


class SettingResponse(BaseModel):
    key: str
    value: dict | str | int | float | bool
    description: str | None = None
    updated_by: str | None = None


DEFAULTS: dict[str, dict | str | int | float | bool] = {
    "small_llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "large_llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    "embedding_model": {"provider": "openai", "model": "text-embedding-3-large"},
    "reranker": {"provider": "bge", "model": "BAAI/bge-reranker-large"},
    "vector_top_k": 20,
    "bm25_top_k": 20,
    "rerank_top_n": 10,
    "chunk_parent_size": 1024,
    "chunk_child_size": 256,
    "chunk_overlap": 32,
}


@router.get("/admin/settings")
async def get_settings_all(
    principal: Principal = Depends(require_role(Role.ADMIN, Role.STEWARD)),
) -> dict:
    """Effective settings: stored overrides layered over the defaults.

    Previously this returned the hard-coded defaults regardless of what had
    been saved, so the page could not show an operator what was actually in
    force — which makes a settings screen actively misleading rather than
    merely incomplete.
    """
    stored = await _load_stored()
    return {
        "settings": [
            {"key": key, "value": stored.get(key, default), "is_override": key in stored}
            for key, default in DEFAULTS.items()
        ]
    }


@router.put("/admin/settings/{key}")
async def update_setting(
    key: str,
    body: SettingValue,
    principal: Principal = Depends(require_role(Role.ADMIN)),
) -> SettingResponse:
    """Persist a setting override, with a full before/after audit entry.

    This endpoint previously logged the change and returned the submitted
    value without writing anything, so a configuration change appeared to
    succeed and then silently did not exist. Governance depends on the
    opposite property: every change is durable and attributable.
    """
    if key not in DEFAULTS:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Unknown setting '{key}'. Known: {sorted(DEFAULTS)}.",
        )
    if key == FLAGS_KEY:
        # Kill switches share this table but have their own audited route;
        # allowing them through here would bypass that audit action.
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Use PUT /governance/flags/{flag} to change runtime flags.",
        )

    stored = await _load_stored()
    before = stored.get(key, DEFAULTS[key])

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            pg_insert(SystemSettingModel)
            .values(
                key=key,
                value={"value": body.value},
                description=f"Operator override for '{key}'.",
                updated_by=principal.user_id,
            )
            .on_conflict_do_update(
                index_elements=[SystemSettingModel.key],
                set_={"value": {"value": body.value}, "updated_by": principal.user_id},
            )
        )
        await session.commit()

    await audit_record(
        action=AuditAction.SETTING_CHANGED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="system_setting",
        resource_id=key,
        outcome=AuditOutcome.COMPLETED,
        before=before,
        after=body.value,
        reason=body.reason,
    )
    logger.info("admin_setting_update", key=key, actor=str(principal.user_id))
    return SettingResponse(key=key, value=body.value, updated_by=str(principal.user_id))


async def _load_stored() -> dict:
    """Read all persisted overrides.

    Values are wrapped as `{"value": ...}` in JSONB because the column is a
    dict while a setting may legitimately be a scalar (`vector_top_k = 20`).
    Unwrapped here so callers never see the envelope.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SystemSettingModel.key, SystemSettingModel.value).where(
                    SystemSettingModel.key != FLAGS_KEY
                )
            )
        ).all()
    return {
        key: (value.get("value") if isinstance(value, dict) and "value" in value else value)
        for key, value in rows
    }
