from __future__ import annotations

from unittest.mock import patch

import pytest

from src.plugins.registry import PluginDisabledError, PluginNotFoundError, PluginRegistry
from src.tools.base import Tool, ToolRequest, ToolResult
from src.tools.registry import (
    available_tools,
    get_tool,
    load_builtin_tools,
    reset_tools,
    tool_catalog,
)


class _Fake(Tool):
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description

    async def execute(self, request: ToolRequest) -> ToolResult:
        return ToolResult(answer="")


@pytest.fixture
def registry():
    """An isolated registry with built-in loading stubbed out, so these tests
    neither see nor disturb the real tool set."""
    fake = PluginRegistry("tool")
    with (
        patch("src.tools.registry.tools", fake),
        patch("src.tools.registry.load_builtin_tools"),
    ):
        yield fake


class TestLoadBuiltinTools:
    def test_importing_the_package_happens_once(self):
        """Called at API startup *and* on the router's first use, so it has to
        be idempotent rather than re-importing on every call."""
        reset_tools()
        with patch("src.plugins.discovery.load_package") as load_package:
            load_builtin_tools()
            load_builtin_tools()

        load_package.assert_called_once()

    def test_reset_allows_a_reload(self):
        reset_tools()
        with patch("src.plugins.discovery.load_package") as load_package:
            load_builtin_tools()
            reset_tools()
            load_builtin_tools()

        assert load_package.call_count == 2

    def test_strict_is_passed_through(self):
        reset_tools()
        with patch("src.plugins.discovery.load_package") as load_package:
            load_builtin_tools(strict=True)

        assert load_package.call_args.kwargs["strict"] is True


class TestAvailableTools:
    def test_lists_every_registered_tool(self, registry):
        registry.add("alpha", lambda: _Fake("alpha", "First."))
        registry.add("beta", lambda: _Fake("beta", "Second."))

        assert [tool.name for tool in available_tools()] == ["alpha", "beta"]

    def test_a_disabled_tool_is_skipped_not_raised(self, registry):
        """This feeds the classifier's menu: offering a tool that cannot run
        would let the router pick it and then fail."""

        def _disabled():
            raise PluginDisabledError("tool", "beta", "tools_beta_enabled")

        registry.add("alpha", lambda: _Fake("alpha", "First."))
        registry.add("beta", _disabled)

        assert [tool.name for tool in available_tools()] == ["alpha"]

    def test_a_tool_that_fails_to_construct_does_not_hide_the_others(self, registry):
        def _broken():
            raise RuntimeError("missing API key")

        registry.add("alpha", lambda: _Fake("alpha", "First."))
        registry.add("broken", _broken)

        assert [tool.name for tool in available_tools()] == ["alpha"]

    def test_an_empty_registry_yields_no_tools(self, registry):
        assert available_tools() == []


class TestGetTool:
    def test_builds_the_named_tool(self, registry):
        registry.add("alpha", lambda: _Fake("alpha", "First."))

        assert get_tool("alpha").name == "alpha"

    def test_an_unknown_name_names_what_is_available(self, registry):
        registry.add("alpha", lambda: _Fake("alpha", "First."))

        with pytest.raises(PluginNotFoundError, match="alpha"):
            get_tool("nope")

    def test_a_disabled_tool_raises_rather_than_being_built(self, registry):
        registry.add(
            "gated", lambda: _Fake("gated", "Gated."), requires_flag="tools_gated_enabled"
        )

        with (
            patch.object(registry, "_flag_enabled", return_value=False),
            pytest.raises(PluginDisabledError),
        ):
            get_tool("gated")


class TestToolCatalog:
    def test_is_built_from_the_live_registry(self, registry):
        """A hardcoded prompt string silently drifts out of date, leaving a
        tool that is installed but never chosen."""
        registry.add("alpha", lambda: _Fake("alpha", "Does the first thing."))
        registry.add("beta", lambda: _Fake("beta", "Does the second thing."))

        assert tool_catalog() == (
            "- alpha: Does the first thing.\n- beta: Does the second thing."
        )

    def test_says_so_explicitly_when_nothing_is_registered(self, registry):
        assert tool_catalog() == "(no tools available)"
