from __future__ import annotations

import pytest

from src.governance import feature_flags
from src.plugins import PluginNotFoundError, PluginRegistry
from src.plugins.registry import PluginDisabledError


@pytest.fixture
def registry() -> PluginRegistry[str]:
    return PluginRegistry("widget")


@pytest.fixture(autouse=True)
def _flags():
    feature_flags.reset()
    yield
    feature_flags.reset()


class TestRegistration:
    def test_decorator_registers_a_factory(self, registry):
        @registry.register("alpha", description="the first widget")
        def _build() -> str:
            return "alpha-instance"

        assert registry.has("alpha")
        assert registry.create("alpha") == "alpha-instance"

    def test_names_are_normalised(self, registry):
        registry.add("  Alpha  ", lambda: "x")
        assert registry.has("alpha")
        assert registry.has("ALPHA")

    def test_factory_receives_kwargs(self, registry):
        registry.add("greet", lambda name: f"hello {name}")
        assert registry.create("greet", name="world") == "hello world"

    def test_duplicate_registration_raises(self, registry):
        """Silently overwriting would make import order decide which
        implementation runs -- a bug that only appears when an import moves."""
        registry.add("alpha", lambda: "first")
        with pytest.raises(ValueError, match="already registered"):
            registry.add("alpha", lambda: "second")

    def test_explicit_replace_is_allowed(self, registry):
        registry.add("alpha", lambda: "first")
        registry.add("alpha", lambda: "second", replace=True)
        assert registry.create("alpha") == "second"


class TestLookupFailures:
    def test_unknown_name_lists_what_is_available(self, registry):
        """The overwhelmingly common cause is a typo or an un-imported plugin
        module, and both are diagnosed instantly from the list."""
        registry.add("alpha", lambda: "x")
        registry.add("beta", lambda: "y")

        with pytest.raises(PluginNotFoundError) as exc:
            registry.create("gamma")
        assert "alpha" in str(exc.value)
        assert "beta" in str(exc.value)

    def test_empty_registry_says_so_explicitly(self, registry):
        with pytest.raises(PluginNotFoundError, match="was the plugin module imported"):
            registry.create("anything")


class TestFeatureFlagGating:
    def test_disabled_plugin_cannot_be_constructed(self, registry):
        """Enforced inside create() so a disabled plugin cannot be built by a
        call path that forgot to check the flag."""
        registry.add("risky", lambda: "boom", requires_flag="enable_web_search")
        feature_flags.set_override("enable_web_search", False)

        with pytest.raises(PluginDisabledError) as exc:
            registry.create("risky")
        assert "enable_web_search" in str(exc.value)

    def test_enabled_plugin_constructs(self, registry):
        registry.add("risky", lambda: "ok", requires_flag="enable_web_search")
        feature_flags.set_override("enable_web_search", True)
        assert registry.create("risky") == "ok"

    def test_ungated_plugin_ignores_flags(self, registry):
        registry.add("plain", lambda: "ok")
        assert registry.create("plain") == "ok"

    def test_disabled_is_distinct_from_missing(self, registry):
        """The operator response differs: 'not found' means fix the name,
        'disabled' means flip the flag."""
        assert not issubclass(PluginDisabledError, PluginNotFoundError)


class TestIntrospection:
    def test_describe_reports_flag_state(self, registry):
        registry.add("risky", lambda: "x", requires_flag="enable_web_search")
        feature_flags.set_override("enable_web_search", False)

        described = {d["name"]: d for d in registry.describe()}
        assert described["risky"]["enabled"] is False
        assert described["risky"]["requires_flag"] == "enable_web_search"

    def test_names_are_sorted(self, registry):
        registry.add("zeta", lambda: "z")
        registry.add("alpha", lambda: "a")
        assert registry.names() == ["alpha", "zeta"]

    def test_supports_len_and_contains(self, registry):
        registry.add("alpha", lambda: "a")
        assert len(registry) == 1
        assert "alpha" in registry
        assert "missing" not in registry
