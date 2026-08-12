from __future__ import annotations

import pytest
from src.evaluation.offline.metrics import _parse_score


class TestScoreParsing:
    @pytest.mark.parametrize(
        "response,expected",
        [
            ("8", 0.8),
            ("10", 1.0),
            ("0", 0.0),
            ("7.5", 0.75),
            ("Score: 9", 0.9),
            ("  6  ", 0.6),
        ],
    )
    def test_parses_and_normalises_to_unit_range(self, response, expected):
        assert _parse_score(response) == pytest.approx(expected)

    def test_unparseable_response_returns_none_not_a_midpoint(self):
        """The old behaviour returned 0.5, inventing a middling score that was
        indistinguishable from a genuinely mediocre answer -- exactly the
        confusion an evaluation system exists to prevent."""
        assert _parse_score("I cannot rate this.") is None
        assert _parse_score("") is None

    def test_none_is_distinguishable_from_a_real_zero(self):
        assert _parse_score("0") == 0.0
        assert _parse_score("no numeric answer") is None
        assert _parse_score("0") is not None

    def test_result_is_clamped_to_unit_range(self):
        value = _parse_score("10")
        assert value is not None
        assert 0.0 <= value <= 1.0
