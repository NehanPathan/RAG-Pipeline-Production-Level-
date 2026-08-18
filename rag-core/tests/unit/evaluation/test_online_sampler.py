from __future__ import annotations

import uuid

from src.evaluation.online.sampler import should_sample


class TestSamplingDecision:
    def test_zero_rate_never_samples(self):
        assert should_sample("abc123", 0.0) is False

    def test_full_rate_always_samples(self):
        assert should_sample("abc123", 1.0) is True

    def test_missing_trace_id_is_not_sampled(self):
        """Without an id there is nothing to correlate the score back to, so
        the sample would be unusable for debugging."""
        assert should_sample("", 0.5) is False

    def test_decision_is_deterministic(self):
        """Same request, same decision -- so a re-run reproduces it and the
        behaviour is testable without patching a random source."""
        trace_id = uuid.uuid4().hex
        decisions = {should_sample(trace_id, 0.5) for _ in range(50)}
        assert len(decisions) == 1

    def test_rate_is_approximately_honoured(self):
        trace_ids = [uuid.UUID(int=i).hex for i in range(4000)]
        sampled = sum(should_sample(t, 0.10) for t in trace_ids)
        # Hash-bucketed, so this is tight; the band is wide enough to never
        # flake but narrow enough to catch an off-by-a-factor-of-ten bug.
        assert 0.06 < sampled / len(trace_ids) < 0.16

    def test_higher_rate_samples_a_superset(self):
        """Bucketing must be monotonic: raising the rate may only add traces,
        never swap which ones are covered."""
        trace_ids = [uuid.UUID(int=i).hex for i in range(1000)]
        low = {t for t in trace_ids if should_sample(t, 0.10)}
        high = {t for t in trace_ids if should_sample(t, 0.50)}
        assert low <= high

    def test_rates_above_one_are_treated_as_always(self):
        assert should_sample("abc", 5.0) is True

    def test_negative_rate_is_treated_as_never(self):
        assert should_sample("abc", -1.0) is False
