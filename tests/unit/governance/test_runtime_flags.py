from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from src.governance.runtime_flags import DEFAULT_FLAGS, RuntimeFlags, reset_flags_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_flags_cache()
    yield
    reset_flags_cache()


class TestRuntimeFlags:
    def test_everything_enabled_by_default(self):
        """A fresh deployment must serve traffic, not sit switched off."""
        assert all(DEFAULT_FLAGS.as_dict().values())

    def test_from_dict_preserves_unspecified_flags(self):
        flags = RuntimeFlags.from_dict({"answering_enabled": False})
        assert flags.answering_enabled is False
        assert flags.retrieval_enabled is True
        assert flags.ingestion_enabled is True

    def test_from_dict_ignores_unknown_keys(self):
        """A stale row containing a flag that has since been removed must not
        break flag loading -- that would disable the kill switches entirely."""
        flags = RuntimeFlags.from_dict({"answering_enabled": False, "retired_flag": True})
        assert flags.answering_enabled is False
        assert "retired_flag" not in flags.as_dict()

    def test_from_dict_coerces_truthy_values(self):
        assert RuntimeFlags.from_dict({"answering_enabled": 0}).answering_enabled is False
        assert RuntimeFlags.from_dict({"answering_enabled": 1}).answering_enabled is True

    def test_round_trips_through_dict(self):
        original = RuntimeFlags(answering_enabled=False, semantic_cache_enabled=False)
        assert RuntimeFlags.from_dict(original.as_dict()) == original

    def test_flags_are_immutable(self):
        """An operator flips a switch through set_flag(), which persists and
        audits it; direct mutation would change one process silently."""
        with pytest.raises(FrozenInstanceError):
            DEFAULT_FLAGS.answering_enabled = False  # type: ignore[misc]

    def test_as_dict_covers_every_field(self):
        assert set(DEFAULT_FLAGS.as_dict()) == {
            "answering_enabled",
            "retrieval_enabled",
            "ingestion_enabled",
            "semantic_cache_enabled",
            "online_eval_enabled",
        }
