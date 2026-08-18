from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from src.domain.value_objects.sensitivity import Sensitivity
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Re-exported so governance call sites can import policy and classification
# from one place; the type itself lives in the domain layer because the
# classification of a document is a property of the data, not of the policy.
__all__ = [
    "AIPolicy",
    "EnforcementMode",
    "PolicyDecision",
    "PolicyViolationError",
    "Sensitivity",
    "get_policy",
    "reset_policy",
]


class EnforcementMode(str, Enum):
    """How a failed policy check behaves.

    MONITOR counts and logs the violation but lets the request through —
    the intended way to roll a new control into production: watch the
    violation counter for a week, confirm the rate is what you expect, then
    flip to ENFORCE. Shipping straight to ENFORCE is how a well-meaning
    control takes down a working system.
    """

    ENFORCE = "enforce"
    MONITOR = "monitor"


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a single policy check.

    `control_id` ties the decision back to a control in the risk register,
    so a blocked request is traceable to the documented risk it protects
    against rather than surfacing as an anonymous 4xx.
    """

    allowed: bool
    control_id: str
    reason: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


class PolicyViolationError(Exception):
    """Raised only where a violation cannot be safely degraded (e.g. an
    unapproved model at startup). Request-path checks return a
    PolicyDecision instead so the caller chooses how to degrade.
    """

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(f"[{decision.control_id}] {decision.reason}")
        self.decision = decision


@dataclass(frozen=True)
class AIPolicy:
    """The single declarative source of truth for what this system may do.

    Every value here is version-controlled configuration rather than a
    sentence in a design doc, so "our faithfulness floor is 0.75" is a fact
    the CI gate, the runtime alarm, and the evaluation dashboard all read
    from the same place. Frozen because a policy that request handlers can
    mutate is not a policy.
    """

    version: str
    enforcement_mode: EnforcementMode

    # GOVERN — approved components. Blocks a silent model swap slipping in
    # via .env without a documented review.
    allowed_llm_providers: frozenset[str]
    allowed_embedding_models: frozenset[str]

    # MEASURE — quality floors. Shared by the CI gate and the runtime alarm.
    min_faithfulness: float
    min_answer_relevancy: float
    min_context_relevancy: float

    # GOVERN — grounding rules on generated answers.
    require_citations: bool
    refuse_when_no_context: bool

    # MAP — data classification defaults and role clearances.
    default_sensitivity: Sensitivity
    default_clearance: Sensitivity
    role_clearance: Mapping[str, Sensitivity]

    # MANAGE — lifecycle and sampling.
    retention_days: int
    online_eval_sample_rate: float
    pii_redaction_enabled: bool

    REFUSAL_MESSAGE = (
        "I can't answer that from the indexed documents. No supporting "
        "passage was retrieved, and this system is configured not to answer "
        "beyond its sources."
    )


    def check_llm_provider(self, provider: str) -> PolicyDecision:
        if provider.lower() in self.allowed_llm_providers:
            return PolicyDecision(True, "C-GOV-01")
        return PolicyDecision(
            False,
            "C-GOV-01",
            f"LLM provider '{provider}' is not on the approved list "
            f"({sorted(self.allowed_llm_providers)}).",
        )

    def check_embedding_model(self, model: str) -> PolicyDecision:
        if model in self.allowed_embedding_models:
            return PolicyDecision(True, "C-GOV-02")
        return PolicyDecision(
            False,
            "C-GOV-02",
            f"Embedding model '{model}' is not on the approved list "
            f"({sorted(self.allowed_embedding_models)}).",
        )

    def check_grounding(self, *, context_chunks: int, valid_citations: int) -> PolicyDecision:
        """Runs after generation, before the answer is released.

        Two distinct failures collapse into one control: nothing was
        retrieved at all (the model had no grounds), and something was
        retrieved but the answer cited none of it (the model ignored its
        grounds). Both mean the answer is ungrounded.
        """
        if self.refuse_when_no_context and context_chunks == 0:
            return PolicyDecision(
                False, "C-GOV-03", "No context was retrieved for this query."
            )
        if self.require_citations and context_chunks > 0 and valid_citations == 0:
            return PolicyDecision(
                False,
                "C-GOV-04",
                "Answer cited none of the retrieved passages.",
            )
        return PolicyDecision(True, "C-GOV-04")


    def clearance_for_role(self, role: str | None) -> Sensitivity:
        """Unknown roles get the configured default, never the maximum."""
        if not role:
            return self.default_clearance
        return self.role_clearance.get(role.lower(), self.default_clearance)

    def check_read(self, sensitivity: Sensitivity, clearance: Sensitivity) -> PolicyDecision:
        if sensitivity.readable_with(clearance):
            return PolicyDecision(True, "C-MAP-01")
        return PolicyDecision(
            False,
            "C-MAP-01",
            f"'{sensitivity.value}' content requires clearance "
            f"'{sensitivity.value}' or above; principal holds '{clearance.value}'.",
        )


    def quality_floor(self, metric_name: str) -> float | None:
        """The configured floor for an evaluation metric, or None if that
        metric is measured but not gated."""
        return {
            "faithfulness": self.min_faithfulness,
            "answer_relevancy": self.min_answer_relevancy,
            "context_relevancy": self.min_context_relevancy,
        }.get(metric_name)

    def check_quality(self, metric_name: str, value: float) -> PolicyDecision:
        floor = self.quality_floor(metric_name)
        if floor is None or value >= floor:
            return PolicyDecision(True, "C-MEA-01")
        return PolicyDecision(
            False,
            "C-MEA-01",
            f"{metric_name}={value:.3f} is below the policy floor of {floor:.2f}.",
        )


    @property
    def enforcing(self) -> bool:
        return self.enforcement_mode is EnforcementMode.ENFORCE

    def as_dict(self) -> dict:
        """Serialisable view — surfaced by GET /governance/policy so the
        policy in force is inspectable at runtime, not just in the repo."""
        return {
            "version": self.version,
            "enforcement_mode": self.enforcement_mode.value,
            "allowed_llm_providers": sorted(self.allowed_llm_providers),
            "allowed_embedding_models": sorted(self.allowed_embedding_models),
            "min_faithfulness": self.min_faithfulness,
            "min_answer_relevancy": self.min_answer_relevancy,
            "min_context_relevancy": self.min_context_relevancy,
            "require_citations": self.require_citations,
            "refuse_when_no_context": self.refuse_when_no_context,
            "default_sensitivity": self.default_sensitivity.value,
            "default_clearance": self.default_clearance.value,
            "role_clearance": {r: s.value for r, s in self.role_clearance.items()},
            "retention_days": self.retention_days,
            "online_eval_sample_rate": self.online_eval_sample_rate,
            "pii_redaction_enabled": self.pii_redaction_enabled,
        }


def _parse_csv_set(raw: str) -> frozenset[str]:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _parse_role_clearance(raw: str, default: Sensitivity) -> Mapping[str, Sensitivity]:
    """Parses `viewer:public,analyst:internal,admin:restricted`."""
    mapping: dict[str, Sensitivity] = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        role, _, level = pair.partition(":")
        if not role.strip() or not level.strip():
            logger.warning("role_clearance_pair_malformed", pair=pair)
            continue
        mapping[role.strip().lower()] = Sensitivity.parse(level, default)
    return MappingProxyType(mapping)


_policy: AIPolicy | None = None


def get_policy() -> AIPolicy:
    """Singleton AIPolicy built from settings on first access.

    Matches the get_settings()/get_langfuse_client() pattern already used
    across the project rather than introducing a DI container.
    """
    global _policy
    if _policy is None:
        from src.config import get_settings

        s = get_settings()
        default_sensitivity = Sensitivity.parse(
            s.governance_default_sensitivity, Sensitivity.INTERNAL
        )
        default_clearance = Sensitivity.parse(
            s.governance_default_clearance, Sensitivity.PUBLIC
        )
        _policy = AIPolicy(
            version=s.governance_policy_version,
            enforcement_mode=EnforcementMode(s.governance_enforcement_mode.lower()),
            allowed_llm_providers=_parse_csv_set(s.governance_allowed_llm_providers),
            allowed_embedding_models=_parse_csv_set(s.governance_allowed_embedding_models),
            min_faithfulness=s.governance_min_faithfulness,
            min_answer_relevancy=s.governance_min_answer_relevancy,
            min_context_relevancy=s.governance_min_context_relevancy,
            require_citations=s.governance_require_citations,
            refuse_when_no_context=s.governance_refuse_when_no_context,
            default_sensitivity=default_sensitivity,
            default_clearance=default_clearance,
            role_clearance=_parse_role_clearance(
                s.governance_role_clearance, default_clearance
            ),
            retention_days=s.governance_retention_days,
            online_eval_sample_rate=s.governance_online_eval_sample_rate,
            pii_redaction_enabled=s.governance_pii_redaction_enabled,
        )
        logger.info(
            "policy_loaded",
            version=_policy.version,
            enforcement_mode=_policy.enforcement_mode.value,
        )
    return _policy


def reset_policy() -> None:
    """Test helper: force re-creation of the singleton on next access."""
    global _policy
    _policy = None
