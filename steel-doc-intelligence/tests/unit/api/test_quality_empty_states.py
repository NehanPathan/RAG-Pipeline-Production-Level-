"""What the quality gate reports when nothing has been measured yet.

The UI reported as "blank" was showing its empty state correctly -- there
simply were no evaluation runs. What the screen could *not* do was tell an
un-measured system apart from a failed request, because it had no error
branch at all.

These pin the API half of that contract. The property that matters is that an
unmeasured gate reports **unknown**, never `False` and never a zero: `passing:
false` renders as a red quality banner on a system nobody has evaluated, and a
zeroed score renders as a measurement that was never taken. A fabricated
quality metric is worse than a blank screen.

The data layer is stubbed rather than reached. These endpoints read Postgres,
and a unit test that opens a connection pool is an integration test wearing
the wrong label -- it also fights the event loop when several run in a row.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import ClassVar

import pytest

from src.api.routes import evaluation as ev


@pytest.fixture
def no_runs(monkeypatch):
    """A database with no evaluation runs in it."""

    @asynccontextmanager
    async def _session():
        yield object()

    async def _list_runs(session):
        return []

    monkeypatch.setattr(ev, "get_session_factory", lambda: _session)
    monkeypatch.setattr(ev, "list_runs", _list_runs)


@pytest.fixture
def one_failing_run(monkeypatch):
    """A completed run whose faithfulness sits below the policy floor."""

    class _Metric:
        metric_name = "faithfulness"
        value = 0.10

    class _Run:
        id = "11111111-1111-1111-1111-111111111111"
        dataset_name = "golden_set_v1"
        status = "completed"
        metrics: ClassVar[list] = [_Metric()]

    @asynccontextmanager
    async def _session():
        yield object()

    async def _list_runs(session):
        return [_Run()]

    monkeypatch.setattr(ev, "get_session_factory", lambda: _session)
    monkeypatch.setattr(ev, "list_runs", _list_runs)


class TestNothingMeasuredYet:
    @pytest.mark.asyncio
    async def test_the_gate_reports_no_completed_run(self, no_runs):
        """The state the UI renders as "No completed run for this dataset",
        distinct from both a failure and a pass."""
        gate = await ev.evaluation_gate()
        assert gate["status"] == "no_completed_run"

    @pytest.mark.asyncio
    async def test_passing_is_unknown_never_false(self, no_runs):
        """`False` would paint a red quality banner over a system that has
        simply never been evaluated. Unknown is the truthful answer."""
        assert (await ev.evaluation_gate())["passing"] is None

    @pytest.mark.asyncio
    async def test_no_metric_is_invented(self, no_runs):
        """The architectural rule: never fabricate quality data. An
        unmeasured gate carries no scores at all -- not zeroed ones, which
        would render as measurements that were never taken."""
        gate = await ev.evaluation_gate()
        assert "metrics" not in gate or not gate["metrics"]
        assert "run_id" not in gate

    @pytest.mark.asyncio
    async def test_the_policy_version_is_still_reported(self, no_runs):
        """Which floors *would* apply is knowable without a run, and it is
        what tells a reader the gate is configured rather than absent."""
        assert (await ev.evaluation_gate())["policy_version"]

    @pytest.mark.asyncio
    async def test_an_unknown_dataset_is_empty_not_an_error(self, one_failing_run):
        """Selecting a dataset nobody has run yet is an ordinary thing to do
        in the picker, and must not read as a failure."""
        gate = await ev.evaluation_gate(dataset_name="never_run_v9")
        assert gate["status"] == "no_completed_run"


class TestMeasuredRuns:
    @pytest.mark.asyncio
    async def test_a_completed_run_is_judged_against_the_floors(self, one_failing_run):
        """The contrast case: with real data the gate reports a real verdict,
        so the empty state above is genuinely about absence of data rather
        than about the gate never working."""
        gate = await ev.evaluation_gate()
        assert gate["status"] != "no_completed_run"
        assert gate["passing"] is False
        assert gate["run_id"]

    @pytest.mark.asyncio
    async def test_each_metric_reports_its_floor(self, one_failing_run):
        gate = await ev.evaluation_gate()
        metric = gate["metrics"][0]
        assert metric["metric"] == "faithfulness"
        assert metric["value"] == 0.1
        assert metric["passing"] is False
