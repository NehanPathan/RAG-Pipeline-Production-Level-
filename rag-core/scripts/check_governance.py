#!/usr/bin/env python
"""Governance consistency check — run in CI, fails the build on drift.

The failure mode this exists to prevent: a risk register that keeps claiming
controls after the code implementing them was renamed or deleted, and metrics
that are declared but never recorded so a dashboard shows a confident flat
zero. Both look fine in review and are only discovered during an incident.

Four checks:

  1. Every metric a risk names is registered in prometheus_metrics.py.
  2. Every `implemented_in` path a control names exists on disk.
  3. Every registered metric has at least one recording call site in src/ --
     a declared-but-unused metric reads as "0, all good".
  4. Every risk carrying a threshold has a matching Prometheus alert rule.

Exit code 0 = clean, 1 = problems found (all of them printed, not just the
first, so one CI run surfaces the whole picture).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.governance.risk_register import load_risk_register  # noqa: E402
from src.monitoring.prometheus_metrics import metric_names, registry  # noqa: E402

ALERTS_PATH = REPO_ROOT / "docker" / "prometheus" / "alerts.yml"
METRICS_MODULE = REPO_ROOT / "src" / "monitoring" / "prometheus_metrics.py"
SRC_DIR = REPO_ROOT / "src"

# Metrics whose only recording path is outside src/ (published by the
# collector, or set from a script). Each entry needs a reason -- this list is
# how check 3 gets defeated, so it should stay short and justified.
RECORDING_EXEMPTIONS: dict[str, str] = {}


def check_register_metrics_exist(register, known: set[str]) -> list[str]:
    return [
        f"risk {risk.id}: metric '{risk.metric}' is not registered in prometheus_metrics.py"
        for risk in register.risks
        if risk.metric and risk.metric not in known
    ]


def check_control_paths(register) -> list[str]:
    problems = []
    for risk in register.risks:
        for control in risk.controls:
            for rel in control.implemented_in:
                if not (REPO_ROOT / rel).exists():
                    problems.append(
                        f"risk {risk.id}/{control.id}: implemented_in path '{rel}' does not exist"
                    )
    return problems


def check_metrics_are_recorded() -> list[str]:
    """Every declared metric must be incremented/observed/set somewhere.

    Matches on the Python identifier rather than the exported metric name,
    because that is what appears at a call site (`llm_tokens.labels(...)`).
    """
    identifiers = _declared_identifiers()
    sources = [
        p for p in SRC_DIR.rglob("*.py") if p != METRICS_MODULE and "__pycache__" not in p.parts
    ]
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in sources)

    problems = []
    for identifier in sorted(identifiers):
        if identifier in RECORDING_EXEMPTIONS:
            continue
        used = re.search(
            rf"\b{re.escape(identifier)}\s*\.\s*(labels|inc|observe|set)\b", blob
        )
        if not used:
            problems.append(
                f"metric '{identifier}' is declared but never recorded -- "
                f"a metric nobody writes reads as 0 on a dashboard"
            )
    return problems


def check_thresholds_have_alerts(register) -> list[str]:
    if not ALERTS_PATH.exists():
        return [f"alert rules file missing: {ALERTS_PATH.relative_to(REPO_ROOT)}"]
    alerts = ALERTS_PATH.read_text(encoding="utf-8")
    return [
        f"risk {risk.id}: has threshold {risk.threshold} but no alert rule references it "
        f"(add a rule labelled risk_id: {risk.id})"
        for risk in register.risks
        if risk.threshold is not None and f"risk_id: {risk.id}" not in alerts
    ]


def _declared_identifiers() -> set[str]:
    """Module-level metric variable names, read from the source.

    Parsed from the file rather than introspected from the registry because
    the registry only knows exported metric names, and the check needs the
    Python identifiers that appear at call sites.
    """
    source = METRICS_MODULE.read_text(encoding="utf-8")
    return set(
        re.findall(r"^([a-z_][a-z0-9_]*)\s*=\s*(?:Counter|Gauge|Histogram|Summary)\(", source, re.M)
    )


def main() -> int:
    register = load_risk_register()
    known = metric_names()

    sections = {
        "Risk metrics not registered": check_register_metrics_exist(register, known),
        "Control paths that do not exist": check_control_paths(register),
        "Declared metrics never recorded": check_metrics_are_recorded(),
        "Thresholds without alert rules": check_thresholds_have_alerts(register),
        "Register self-validation": register.validate(known, REPO_ROOT),
    }

    total = sum(len(v) for v in sections.values())

    print("=" * 72)
    print(f"Governance check — register v{register.version}, {len(register.risks)} risks")
    print(f"  metrics registered : {len(_declared_identifiers())}")
    print(f"  controls declared  : {len(register.control_ids)}")
    print(f"  collectors in registry: {len(list(registry.collect()))}")
    print("=" * 72)

    for title, problems in sections.items():
        status = "OK" if not problems else f"{len(problems)} PROBLEM(S)"
        print(f"\n[{status}] {title}")
        for problem in problems:
            print(f"    - {problem}")

    print("\n" + "=" * 72)
    if total:
        print(f"FAILED: {total} governance problem(s) found.")
        return 1
    print("PASSED: risk register, metrics and alert rules are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
