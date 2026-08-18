from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_REGISTER_PATH = Path(__file__).parents[2] / "docs" / "governance" / "risk_register.yaml"

VALID_FUNCTIONS = {"govern", "map", "measure", "manage"}
VALID_DIRECTIONS = {"min", "max"}


@dataclass(frozen=True)
class Control:
    """One mitigation, and where it actually lives in the codebase.

    `implemented_in` is validated against the filesystem by the CI
    governance check. That check is the whole point of this structure: it
    makes it impossible for the register to keep claiming a control that was
    deleted or never written, which is the usual failure mode of a risk
    document maintained by hand.
    """

    id: str
    description: str
    implemented_in: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Risk:
    """One identified risk, bound to the metric that watches it.

    A risk with no metric is unmanaged; a metric watching no risk is noise.
    Requiring both fields on every entry is what turns the register from
    prose into the MAP function's machine-readable output.
    """

    id: str
    title: str
    function: str
    severity: str
    likelihood: str
    description: str
    metric: str
    owner: str
    controls: list[Control] = field(default_factory=list)
    threshold: float | None = None
    threshold_direction: str = "min"
    residual_risk: str = ""

    def breached(self, value: float) -> bool:
        """Whether an observed value violates this risk's threshold."""
        if self.threshold is None:
            return False
        if self.threshold_direction == "min":
            return value < self.threshold
        return value > self.threshold


@dataclass(frozen=True)
class RiskRegister:
    version: str
    risks: list[Risk]

    def by_id(self, risk_id: str) -> Risk | None:
        return next((r for r in self.risks if r.id == risk_id), None)

    def by_function(self, function: str) -> list[Risk]:
        return [r for r in self.risks if r.function == function]

    @property
    def metrics(self) -> set[str]:
        return {r.metric for r in self.risks}

    @property
    def control_ids(self) -> set[str]:
        return {c.id for r in self.risks for c in r.controls}

    def validate(self, known_metrics: set[str], repo_root: Path) -> list[str]:
        """Return every consistency problem found; empty list means clean.

        Returns problems rather than raising so the CI check can report all
        of them in one run instead of one per commit.
        """
        problems: list[str] = []
        seen_ids: set[str] = set()

        for risk in self.risks:
            where = f"risk {risk.id}"
            if risk.id in seen_ids:
                problems.append(f"{where}: duplicate risk id")
            seen_ids.add(risk.id)

            if risk.function not in VALID_FUNCTIONS:
                problems.append(
                    f"{where}: function '{risk.function}' is not one of {sorted(VALID_FUNCTIONS)}"
                )
            if risk.threshold_direction not in VALID_DIRECTIONS:
                problems.append(
                    f"{where}: threshold_direction '{risk.threshold_direction}' "
                    f"is not one of {sorted(VALID_DIRECTIONS)}"
                )
            if not risk.owner:
                problems.append(f"{where}: no owner — an unowned risk is unmanaged")
            if risk.metric not in known_metrics:
                problems.append(
                    f"{where}: metric '{risk.metric}' is not registered in "
                    f"src/monitoring/prometheus_metrics.py"
                )
            if not risk.controls:
                problems.append(f"{where}: no controls — an unmitigated risk needs an explicit note")

            for control in risk.controls:
                if not control.implemented_in:
                    problems.append(
                        f"{where}/{control.id}: no implemented_in paths — "
                        f"a control with no implementation is a claim, not a control"
                    )
                for rel_path in control.implemented_in:
                    if not (repo_root / rel_path).exists():
                        problems.append(
                            f"{where}/{control.id}: implemented_in path "
                            f"'{rel_path}' does not exist"
                        )
        return problems


def load_risk_register(path: Path | None = None) -> RiskRegister:
    source = path or _REGISTER_PATH
    if not source.exists():
        raise FileNotFoundError(f"Risk register not found: {source}")

    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    risks = [
        Risk(
            id=item["id"],
            title=item["title"],
            function=str(item.get("function", "")).lower(),
            severity=item.get("severity", "unknown"),
            likelihood=item.get("likelihood", "unknown"),
            description=item.get("description", ""),
            metric=item.get("metric", ""),
            owner=item.get("owner", ""),
            threshold=item.get("threshold"),
            threshold_direction=str(item.get("threshold_direction", "min")).lower(),
            residual_risk=item.get("residual_risk", ""),
            controls=[
                Control(
                    id=c["id"],
                    description=c.get("description", ""),
                    implemented_in=list(c.get("implemented_in", [])),
                )
                for c in item.get("controls", [])
            ],
        )
        for item in raw.get("risks", [])
    ]
    return RiskRegister(version=str(raw.get("version", "0")), risks=risks)


_register: RiskRegister | None = None


def get_risk_register() -> RiskRegister:
    global _register
    if _register is None:
        _register = load_risk_register()
        logger.info("risk_register_loaded", version=_register.version, risks=len(_register.risks))
    return _register


def reset_risk_register() -> None:
    """Test helper: force re-load on next access."""
    global _register
    _register = None
