from __future__ import annotations

import pytest

from src.governance import feature_flags
from src.governance.feature_flags import FlagScope, UnknownFlagError


@pytest.fixture(autouse=True)
def _reset():
    feature_flags.reset()
    yield
    feature_flags.reset()


class TestVocabulary:
    def test_flag_names_are_unique(self):
        names = [spec.name for spec in feature_flags.FLAGS]
        assert len(names) == len(set(names))

    def test_every_flag_has_a_description(self):
        """A flag nobody can explain is a flag nobody dares change."""
        for spec in feature_flags.FLAGS:
            assert spec.description, f"{spec.name} has no description"

    def test_requested_capability_flags_exist(self):
        for name in (
            "enable_web_search",
            "enable_reranker",
            "enable_guardrails",
            "enable_online_eval",
        ):
            assert name in feature_flags.FLAGS_BY_NAME

    def test_high_blast_radius_capabilities_default_off(self):
        """Web search sends queries to a third party; the SQL tool turns model
        output into database queries. Both should be a decision, not a
        default someone inherits."""
        assert feature_flags.FLAGS_BY_NAME["enable_web_search"].default is False
        assert feature_flags.FLAGS_BY_NAME["enable_sql_tool"].default is False

    def test_kill_switches_default_on(self):
        """A fresh deployment must serve traffic, not sit switched off."""
        for spec in feature_flags.FLAGS:
            if spec.scope is FlagScope.KILL_SWITCH:
                assert spec.default is True, f"{spec.name} defaults off"


class TestResolution:
    def test_reads_the_environment_default(self):
        assert feature_flags.is_enabled("enable_calculator") is True

    def test_override_wins_over_the_default(self):
        feature_flags.set_override("enable_calculator", False)
        assert feature_flags.is_enabled("enable_calculator") is False

    def test_unknown_flag_raises_rather_than_reading_false(self):
        """A typo'd flag name silently disabling a feature is a bug that
        takes a long time to find."""
        with pytest.raises(UnknownFlagError):
            feature_flags.is_enabled("enable_teleportation")

    def test_setting_an_unknown_override_raises(self):
        with pytest.raises(UnknownFlagError):
            feature_flags.set_override("enable_teleportation", True)


class TestStoredOverrides:
    def test_apply_overrides_replaces_the_whole_set(self):
        feature_flags.set_override("enable_calculator", False)
        feature_flags.apply_overrides({"enable_web_search": True})

        assert feature_flags.is_enabled("enable_web_search") is True
        # The earlier override is gone, so the default applies again.
        assert feature_flags.is_enabled("enable_calculator") is True

    def test_unknown_stored_override_is_ignored_not_fatal(self):
        """A stale row naming a removed flag must never break flag loading —
        that would take out the kill switches at exactly the moment someone
        needs them."""
        feature_flags.apply_overrides(
            {"enable_web_search": True, "flag_that_was_deleted": True}
        )
        assert feature_flags.is_enabled("enable_web_search") is True
        assert feature_flags.is_enabled("answering_enabled") is True

    def test_empty_overrides_are_safe(self):
        feature_flags.apply_overrides({})
        assert feature_flags.is_enabled("answering_enabled") is True


class TestIntrospection:
    def test_describe_reports_source(self):
        feature_flags.set_override("enable_calculator", False)
        described = {d["name"]: d for d in feature_flags.describe()}

        assert described["enable_calculator"]["source"] == "override"
        assert described["enable_web_search"]["source"] == "environment"

    def test_describe_covers_every_flag(self):
        assert len(feature_flags.describe()) == len(feature_flags.FLAGS)

    def test_all_flags_returns_resolved_values(self):
        feature_flags.set_override("enable_calculator", False)
        values = feature_flags.all_flags()
        assert values["enable_calculator"] is False
        assert set(values) == set(feature_flags.FLAGS_BY_NAME)
