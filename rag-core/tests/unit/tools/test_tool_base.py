from __future__ import annotations

import pytest

from src.tools.base import Tool, ToolError, ToolRequest, ToolResult


class _Succeeds(Tool):
    name = "ok_tool"
    description = "Always works."

    async def execute(self, request: ToolRequest) -> ToolResult:
        return ToolResult(answer=f"answered: {request.query}", data={"seen": request.query})


class _RaisesToolError(Tool):
    name = "expected_failure_tool"
    description = "Fails in a way the caller should surface."

    async def execute(self, request: ToolRequest) -> ToolResult:
        raise ToolError("Upstream returned 429.")


class _Crashes(Tool):
    name = "buggy_tool"
    description = "Has a defect."
    is_external = True
    requires_flag = "tools_web_search_enabled"

    async def execute(self, request: ToolRequest) -> ToolResult:
        raise KeyError("results")


class TestToolResult:
    def test_defaults_to_a_successful_cacheable_answer(self):
        result = ToolResult(answer="42")

        assert result.succeeded is True
        assert result.cacheable is True
        assert result.data == {}
        assert result.citations == []


class TestRun:
    async def test_returns_what_execute_produced(self):
        result = await _Succeeds().run(ToolRequest(query="what is 2+2"))

        assert result.answer == "answered: what is 2+2"
        assert result.succeeded is True

    async def test_an_expected_failure_surfaces_its_message(self):
        result = await _RaisesToolError().run(ToolRequest(query="anything"))

        assert result.succeeded is False
        assert result.error == "Upstream returned 429."
        assert result.answer == ""

    async def test_a_failed_result_is_never_cached(self):
        """Caching a failure would serve the error back with total confidence
        for as long as the entry lives."""
        result = await _RaisesToolError().run(ToolRequest(query="anything"))

        assert result.cacheable is False

    async def test_an_unexpected_exception_is_contained(self):
        """A defect in one tool must not take down the request path."""
        result = await _Crashes().run(ToolRequest(query="anything"))

        assert result.succeeded is False
        assert result.cacheable is False

    async def test_an_unexpected_exception_does_not_leak_internals_to_the_user(self):
        result = await _Crashes().run(ToolRequest(query="anything"))

        assert result.error == "buggy_tool failed unexpectedly."
        assert "KeyError" not in result.error

    async def test_a_long_query_is_truncated_before_it_reaches_the_trace(self):
        result = await _Succeeds().run(ToolRequest(query="x" * 5000))

        assert result.succeeded is True


class TestToolRequest:
    def test_carries_the_principal_so_a_tool_can_authorize(self):
        """The SQL tool needs to know who is asking; a tool that cannot see the
        caller cannot enforce anything."""
        request = ToolRequest(query="select", principal=None, args={"limit": 10})

        assert request.args == {"limit": 10}
        assert request.trace_id == ""


class TestDescribe:
    def test_reports_the_fields_the_router_gates_on(self):
        assert _Crashes().describe() == {
            "name": "buggy_tool",
            "description": "Has a defect.",
            "requires_flag": "tools_web_search_enabled",
            "is_external": True,
        }

    def test_a_local_tool_declares_no_flag_and_no_external_reach(self):
        described = _Succeeds().describe()

        assert described["requires_flag"] is None
        assert described["is_external"] is False


def test_tool_cannot_be_instantiated_without_execute():
    class _Incomplete(Tool):
        name = "incomplete"

    with pytest.raises(TypeError):
        _Incomplete()
