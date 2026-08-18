from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.governance import feature_flags
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import kill_switch_state

logger = get_logger(__name__)

SETTINGS_KEY = "runtime_flags"
_CACHE_TTL_SECONDS = 10.0

# The kill switches, in the order an operator thinks about them. These are a
# subset of the full flag vocabulary in feature_flags.py -- this module is the
# *persistence and hot-path view* of that vocabulary, not a second one.
_KILL_SWITCHES = ("answering_enabled", "retrieval_enabled", "ingestion_enabled")

# `RuntimeFlags` predates the feature-flag system and its field names are
# baked into call sites across the pipeline. Rather than churn those, the two
# capability flags it exposes are mapped onto their canonical names.
_FIELD_TO_FLAG = {
    "answering_enabled": "answering_enabled",
    "retrieval_enabled": "retrieval_enabled",
    "ingestion_enabled": "ingestion_enabled",
    "semantic_cache_enabled": "enable_semantic_cache",
    "online_eval_enabled": "enable_online_eval",
}


@dataclass(frozen=True)
class RuntimeFlags:
    """MANAGE — switches an operator can flip during an incident.

    Deliberately DB-backed rather than environment variables: an incident
    response that requires a redeploy is not an incident response. The
    10-second cache means a flip takes effect within one cache window
    everywhere, without adding a database round-trip to every query.
    """

    answering_enabled: bool = True
    retrieval_enabled: bool = True
    ingestion_enabled: bool = True
    semantic_cache_enabled: bool = True
    online_eval_enabled: bool = True

    def as_dict(self) -> dict[str, bool]:
        return {
            "answering_enabled": self.answering_enabled,
            "retrieval_enabled": self.retrieval_enabled,
            "ingestion_enabled": self.ingestion_enabled,
            "semantic_cache_enabled": self.semantic_cache_enabled,
            "online_eval_enabled": self.online_eval_enabled,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> RuntimeFlags:
        defaults = cls()
        return cls(
            **{
                name: bool(raw.get(name, getattr(defaults, name)))
                for name in defaults.as_dict()
            }
        )

    @classmethod
    def from_feature_flags(cls) -> RuntimeFlags:
        """Project the current feature-flag values onto this view."""
        return cls(
            **{
                field: feature_flags.is_enabled(flag)
                for field, flag in _FIELD_TO_FLAG.items()
            }
        )


DEFAULT_FLAGS = RuntimeFlags()

_cached_at: float = 0.0


async def get_flags(force_refresh: bool = False) -> RuntimeFlags:
    """Current flags, refreshed from the database at most every 10s.

    On any read failure the last known value is kept rather than reverting
    to defaults — a database blip must not silently re-enable a subsystem an
    operator deliberately disabled.
    """
    global _cached_at

    if not force_refresh and (time.monotonic() - _cached_at) < _CACHE_TTL_SECONDS:
        return RuntimeFlags.from_feature_flags()

    try:
        from src.infrastructure.database.postgres.models import SystemSettingModel

        session_factory = get_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(SystemSettingModel.value).where(SystemSettingModel.key == SETTINGS_KEY)
            )
            row = result.first()
        stored = row[0] if row and row[0] else {}
        feature_flags.apply_overrides(_to_canonical(stored))
        _cached_at = time.monotonic()
        _publish()
    except Exception as exc:
        logger.warning("runtime_flags_read_failed", error=str(exc))

    return RuntimeFlags.from_feature_flags()


async def set_flag(name: str, enabled: bool) -> RuntimeFlags:
    """Flip one flag and persist the full override set. Returns the new view.

    Accepts either a `RuntimeFlags` field name (`semantic_cache_enabled`) or
    a canonical flag name (`enable_semantic_cache`), because the admin UI and
    the governance API grew up naming them differently and rejecting one of
    them would be a gratuitous trap.

    The caller records the audit entry — this function is intentionally
    unaware of who is calling it so it stays usable from scripts and startup
    code as well as from the admin route.
    """
    canonical = _FIELD_TO_FLAG.get(name, name)
    if canonical not in feature_flags.FLAGS_BY_NAME:
        raise KeyError(
            f"Unknown flag {name!r}. Known: "
            f"{sorted(set(_FIELD_TO_FLAG) | set(feature_flags.FLAGS_BY_NAME))}"
        )

    await get_flags(force_refresh=True)
    feature_flags.set_override(canonical, enabled)
    await _persist(feature_flags.all_flags())

    _cached_at = time.monotonic()
    _publish()
    logger.warning("runtime_flag_changed", flag=canonical, enabled=enabled)
    return RuntimeFlags.from_feature_flags()


async def _persist(values: dict[str, bool]) -> None:
    from src.infrastructure.database.postgres.models import SystemSettingModel

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            pg_insert(SystemSettingModel)
            .values(
                key=SETTINGS_KEY,
                value=values,
                description="Feature flags and operator kill switches (governance MANAGE).",
            )
            .on_conflict_do_update(
                index_elements=[SystemSettingModel.key], set_={"value": values}
            )
        )
        await session.commit()


def _to_canonical(stored: dict) -> dict[str, bool]:
    """Translate a persisted row into canonical flag names.

    Rows written before the feature-flag system used the `RuntimeFlags`
    field names, so both spellings have to be readable or an upgrade would
    silently drop whatever an operator had already switched off.
    """
    return {
        _FIELD_TO_FLAG.get(name, name): bool(value)
        for name, value in (stored or {}).items()
    }


def _publish() -> None:
    """Mirror kill-switch state into its own series so a dashboard shows
    *why* traffic stopped, instead of only that it did."""
    for name in _KILL_SWITCHES:
        kill_switch_state.labels(flag=name).set(1.0 if feature_flags.is_enabled(name) else 0.0)


def reset_flags_cache() -> None:
    """Test helper: drop the cached value so the next read hits the DB."""
    global _cached_at
    _cached_at = 0.0
    feature_flags.reset()
