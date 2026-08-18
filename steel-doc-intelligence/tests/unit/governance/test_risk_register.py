from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.governance.risk_register import Control, Risk, RiskRegister, load_risk_register

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def register() -> RiskRegister:
    return load_risk_register()


class TestRealRegister:
    def test_loads_and_is_not_empty(self, register):
        assert register.version
        assert len(register.risks) >= 5

    def test_every_risk_covers_a_valid_gm3_function(self, register):
        assert {r.function for r in register.risks} <= {"govern", "map", "measure", "manage"}

    def test_all_four_functions_are_represented(self, register):
        """A register that only covers one function is not a GM3 register."""
        for function in ("govern", "map", "measure", "manage"):
            assert register.by_function(function), f"no risks for {function}"

    def test_every_risk_has_owner_metric_and_controls(self, register):
        for risk in register.risks:
            assert risk.owner, f"{risk.id} has no owner"
            assert risk.metric, f"{risk.id} has no metric"
            assert risk.controls, f"{risk.id} has no controls"

    def test_risk_ids_are_unique(self, register):
        ids = [r.id for r in register.risks]
        assert len(ids) == len(set(ids))

    def test_validates_clean_against_real_metrics(self, register):
        from src.monitoring.prometheus_metrics import metric_names

        assert register.validate(metric_names(), REPO_ROOT) == []

    def test_yaml_is_wellformed(self):
        raw = yaml.safe_load(
            (REPO_ROOT / "docs" / "governance" / "risk_register.yaml").read_text(encoding="utf-8")
        )
        assert "risks" in raw


class TestThresholdBreach:
    def test_min_direction_breaches_below(self):
        risk = Risk(
            id="X", title="t", function="measure", severity="high", likelihood="probable",
            description="", metric="m", owner="o", threshold=0.75, threshold_direction="min",
        )
        assert risk.breached(0.74) is True
        assert risk.breached(0.75) is False
        assert risk.breached(0.90) is False

    def test_max_direction_breaches_above(self):
        risk = Risk(
            id="X", title="t", function="measure", severity="high", likelihood="probable",
            description="", metric="m", owner="o", threshold=0.05, threshold_direction="max",
        )
        assert risk.breached(0.06) is True
        assert risk.breached(0.05) is False

    def test_no_threshold_never_breaches(self):
        risk = Risk(
            id="X", title="t", function="govern", severity="low", likelihood="rare",
            description="", metric="m", owner="o",
        )
        assert risk.breached(999.0) is False


class TestValidation:
    def test_flags_unknown_metric(self):
        reg = RiskRegister(
            version="1",
            risks=[
                Risk(
                    id="R1", title="t", function="measure", severity="high",
                    likelihood="probable", description="", metric="rag_does_not_exist",
                    owner="team", controls=[Control("C1", "d", ["pyproject.toml"])],
                )
            ],
        )
        problems = reg.validate({"rag_real_metric"}, REPO_ROOT)
        assert any("rag_does_not_exist" in p for p in problems)

    def test_flags_missing_control_path(self):
        reg = RiskRegister(
            version="1",
            risks=[
                Risk(
                    id="R1", title="t", function="map", severity="high", likelihood="probable",
                    description="", metric="m", owner="team",
                    controls=[Control("C1", "d", ["src/does/not/exist.py"])],
                )
            ],
        )
        problems = reg.validate({"m"}, REPO_ROOT)
        assert any("does not exist" in p for p in problems)

    def test_flags_unowned_risk(self):
        reg = RiskRegister(
            version="1",
            risks=[
                Risk(
                    id="R1", title="t", function="map", severity="high", likelihood="probable",
                    description="", metric="m", owner="",
                    controls=[Control("C1", "d", ["pyproject.toml"])],
                )
            ],
        )
        assert any("no owner" in p for p in reg.validate({"m"}, REPO_ROOT))

    def test_flags_control_with_no_implementation(self):
        reg = RiskRegister(
            version="1",
            risks=[
                Risk(
                    id="R1", title="t", function="map", severity="high", likelihood="probable",
                    description="", metric="m", owner="team",
                    controls=[Control("C1", "a claim with no code", [])],
                )
            ],
        )
        assert any("no implemented_in" in p for p in reg.validate({"m"}, REPO_ROOT))

    def test_flags_duplicate_ids(self):
        risk = Risk(
            id="R1", title="t", function="map", severity="high", likelihood="probable",
            description="", metric="m", owner="team",
            controls=[Control("C1", "d", ["pyproject.toml"])],
        )
        reg = RiskRegister(version="1", risks=[risk, risk])
        assert any("duplicate risk id" in p for p in reg.validate({"m"}, REPO_ROOT))

    def test_clean_register_reports_no_problems(self):
        reg = RiskRegister(
            version="1",
            risks=[
                Risk(
                    id="R1", title="t", function="manage", severity="low", likelihood="rare",
                    description="", metric="m", owner="team",
                    controls=[Control("C1", "d", ["pyproject.toml"])],
                )
            ],
        )
        assert reg.validate({"m"}, REPO_ROOT) == []
