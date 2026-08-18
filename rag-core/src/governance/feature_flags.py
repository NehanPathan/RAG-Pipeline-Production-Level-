from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import feature_flag_state

logger = get_logger(__name__)


class FlagScope(str, Enum):
    """Why a flag exists, which determines who may change it and how fast.

    CAPABILITY  turns a feature on or off. Product decision, set per
                environment, changed by deploy or by an admin.
    KILL_SWITCH stops something that is misbehaving. Incident decision,
                changed in seconds without a deploy, always audited.

    Keeping them in one system but distinguishing the scope avoids the two
    common failure modes: a capability toggle that requires a redeploy
    during an incident, and a kill switch buried in a config file where
    nobody thinks to look for it.
    """

    CAPABILITY = "capability"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class FlagSpec:
    name: str
    default: bool
    scope: FlagScope
    description: str


# The full flag vocabulary. Closed set on purpose: a typo'd flag name in a
# call site should raise, not silently read as False and disable a feature
# nobody realises is off.
FLAGS: tuple[FlagSpec, ...] = (
    FlagSpec(
        "enable_query_router",
        True,
        FlagScope.CAPABILITY,
        "Route queries by intent instead of sending everything through RAG.",
    ),
    FlagSpec(
        "enable_web_search",
        False,
        FlagScope.CAPABILITY,
        "Allow the router to answer from live web search. Off by default: it "
        "sends user queries to a third party and costs money per call.",
    ),
    FlagSpec(
        "enable_sql_tool",
        False,
        FlagScope.CAPABILITY,
        "Allow read-only SQL queries against allow-listed tables. Off by "
        "default because it widens the blast radius of a prompt injection.",
    ),
    FlagSpec(
        "enable_calculator",
        True,
        FlagScope.CAPABILITY,
        "Answer arithmetic directly instead of paying for a model call.",
    ),
    FlagSpec(
        "enable_reranker",
        True,
        FlagScope.CAPABILITY,
        "Cross-encoder reranking after fusion. Disabling trades quality for latency.",
    ),
    FlagSpec(
        "enable_guardrails",
        True,
        FlagScope.CAPABILITY,
        "Grounding and citation policy checks on generated answers.",
    ),
    FlagSpec(
        "enable_online_eval",
        True,
        FlagScope.CAPABILITY,
        "Judge a sample of live answers for quality drift.",
    ),
    FlagSpec(
        "enable_semantic_cache",
        True,
        FlagScope.CAPABILITY,
        "Serve near-duplicate queries from cache.",
    ),
    FlagSpec(
        "enable_llm_fallback",
        True,
        FlagScope.CAPABILITY,
        "Let the AI gateway fail over to a secondary provider when the "
        "primary errors. Disable to make provider outages loud instead of silent.",
    ),
    FlagSpec(
        "answering_enabled",
        True,
        FlagScope.KILL_SWITCH,
        "Master switch. Off means the system refuses every query.",
    ),
    FlagSpec(
        "retrieval_enabled",
        True,
        FlagScope.KILL_SWITCH,
        "Off means no document retrieval happens.",
    ),
    FlagSpec(
        "ingestion_enabled",
        True,
        FlagScope.KILL_SWITCH,
        "Off means uploads are rejected.",
    ),
)

FLAGS_BY_NAME: dict[str, FlagSpec] = {spec.name: spec for spec in FLAGS}

# Settings field name for each flag, e.g. enable_web_search -> feature_enable_web_search
_SETTINGS_PREFIX = "feature_"

_overrides: dict[str, bool] = {}
_env_defaults: dict[str, bool] | None = None


class UnknownFlagError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"Unknown feature flag {name!r}. Known flags: {sorted(FLAGS_BY_NAME)}"
        )
        self.name = name


def _load_env_defaults() -> dict[str, bool]:
    """Per-environment defaults, read from settings once.

    Each flag maps to a `feature_<name>` setting, so an environment sets its
    own baseline (`FEATURE_ENABLE_WEB_SEARCH=true` in staging, absent in
    prod) without any code change.
    """
    global _env_defaults
    if _env_defaults is None:
        from src.config import get_settings

        settings = get_settings()
        _env_defaults = {
            spec.name: bool(getattr(settings, f"{_SETTINGS_PREFIX}{spec.name}", spec.default))
            for spec in FLAGS
        }
        for name, enabled in _env_defaults.items():
            feature_flag_state.labels(flag=name).set(1.0 if enabled else 0.0)
        logger.info("feature_flags_loaded", **_env_defaults)
    return _env_defaults


def is_enabled(name: str) -> bool:
    """Current value: runtime override if set, else the environment default.

    Synchronous by design. This is called on hot paths (per query, per plugin
    construction), so it reads an in-process dict; the async
    `refresh_overrides()` is what pulls database changes into that dict.
    """
    if name not in FLAGS_BY_NAME:
        raise UnknownFlagError(name)
    if name in _overrides:
        return _overrides[name]
    return _load_env_defaults()[name]


def all_flags() -> dict[str, bool]:
    defaults = _load_env_defaults()
    return {name: _overrides.get(name, defaults[name]) for name in FLAGS_BY_NAME}


def describe() -> list[dict]:
    """Inventory for the admin API: value, source, scope and why it exists."""
    defaults = _load_env_defaults()
    return [
        {
            "name": spec.name,
            "enabled": _overrides.get(spec.name, defaults[spec.name]),
            "default": defaults[spec.name],
            "source": "override" if spec.name in _overrides else "environment",
            "scope": spec.scope.value,
            "description": spec.description,
        }
        for spec in FLAGS
    ]


def set_override(name: str, enabled: bool) -> None:
    """Set an in-process override. Persistence is the caller's job.

    Split deliberately: `runtime_flags.set_flag()` writes to the database and
    then calls this, while tests and startup can set values without touching
    Postgres.
    """
    if name not in FLAGS_BY_NAME:
        raise UnknownFlagError(name)
    _overrides[name] = bool(enabled)
    feature_flag_state.labels(flag=name).set(1.0 if enabled else 0.0)
    logger.warning("feature_flag_override_set", flag=name, enabled=enabled)


def apply_overrides(values: dict[str, bool]) -> None:
    """Replace all overrides at once, ignoring names that no longer exist.

    A stale database row naming a removed flag must not break flag loading —
    that would take out every switch, including the kill switches, at exactly
    the moment someone needs them.
    """
    _overrides.clear()
    defaults = _load_env_defaults()
    for name, enabled in (values or {}).items():
        if name not in FLAGS_BY_NAME:
            logger.warning("feature_flag_override_unknown_ignored", flag=name)
            continue
        _overrides[name] = bool(enabled)
    for name in FLAGS_BY_NAME:
        value = _overrides.get(name, defaults[name])
        feature_flag_state.labels(flag=name).set(1.0 if value else 0.0)


def reset() -> None:
    """Test helper: drop overrides and force settings to be re-read."""
    global _env_defaults
    _overrides.clear()
    _env_defaults = None
