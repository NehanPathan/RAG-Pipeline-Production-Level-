from __future__ import annotations

from src.api.middleware.trace_context import _sanitise
from src.monitoring.tracing import get_current_trace_id, reset_tracing, set_current_trace_id


class TestTraceHeaderSanitisation:
    def test_accepts_a_plain_hex_id(self):
        assert _sanitise("a3f9c2b1d4e5f6a7") == "a3f9c2b1d4e5f6a7"

    def test_accepts_a_dashed_uuid(self):
        value = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        assert _sanitise(value) == value

    def test_strips_surrounding_whitespace(self):
        assert _sanitise("  abc123  ") == "abc123"

    def test_rejects_newlines(self):
        """The value is echoed into the log stream; a newline would let a
        caller forge log entries."""
        assert _sanitise("abc\nlevel=critical fake=1") == ""

    def test_rejects_non_hex_characters(self):
        assert _sanitise("<script>alert(1)</script>") == ""
        assert _sanitise("../../etc/passwd") == ""

    def test_rejects_oversized_values(self):
        assert _sanitise("a" * 65) == ""

    def test_accepts_max_length_value(self):
        assert _sanitise("a" * 64) == "a" * 64

    def test_missing_header_yields_empty(self):
        assert _sanitise(None) == ""
        assert _sanitise("") == ""


class TestTraceIdContext:
    def setup_method(self):
        reset_tracing()

    def teardown_method(self):
        reset_tracing()

    def test_bound_id_is_readable(self):
        set_current_trace_id("deadbeef")
        assert get_current_trace_id() == "deadbeef"

    def test_returns_empty_when_unset_rather_than_raising(self):
        """Audit and evaluation both call this outside any request; it must
        degrade to an empty string, not blow up the caller."""
        assert get_current_trace_id() == ""
