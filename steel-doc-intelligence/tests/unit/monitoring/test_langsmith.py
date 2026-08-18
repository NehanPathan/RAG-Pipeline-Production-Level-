import os

import pytest

from src.config import Settings
from src.monitoring.langsmith import configure_langsmith

_VARS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGCHAIN_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {name: os.environ.get(name) for name in _VARS}
    for name in _VARS:
        os.environ.pop(name, None)
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class TestTracingIsOffByDefault:
    """LangChain enables LangSmith export from the environment alone. For this
    system that would ship prompt text and retrieved drawing content to a
    third party as a side effect of a variable someone set to run an eval."""

    def test_disabled_when_nothing_is_configured(self):
        assert configure_langsmith(_settings()) is False
        assert os.environ["LANGSMITH_TRACING"] == "false"
        assert os.environ["LANGCHAIN_TRACING_V2"] == "false"

    def test_an_api_key_alone_does_not_enable_tracing(self):
        """A key is needed to run evaluations. It must not, by itself, start
        exporting every production request."""
        enabled = configure_langsmith(_settings(langsmith_api_key="ls-test"))

        assert enabled is False
        assert os.environ["LANGSMITH_TRACING"] == "false"

    def test_a_preexisting_env_var_is_actively_overridden(self):
        """Not merely 'not set' -- actively disabled, so a stale variable in a
        deployment's environment cannot re-enable export behind our backs."""
        os.environ["LANGCHAIN_TRACING_V2"] = "true"

        configure_langsmith(_settings())

        assert os.environ["LANGCHAIN_TRACING_V2"] == "false"

    def test_requesting_tracing_without_a_key_stays_disabled(self):
        assert configure_langsmith(_settings(langsmith_tracing=True)) is False


class TestTracingWhenExplicitlyEnabled:
    def test_enabled_only_with_both_flag_and_key(self):
        settings = _settings(
            langsmith_tracing=True,
            langsmith_api_key="ls-test",
            langsmith_project="steel-test",
        )

        assert configure_langsmith(settings) is True
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_PROJECT"] == "steel-test"

    def test_legacy_aliases_are_set_too(self):
        """Older langchain-core builds read the LANGCHAIN_* names."""
        settings = _settings(langsmith_tracing=True, langsmith_api_key="ls-test")

        configure_langsmith(settings)

        assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
        assert os.environ["LANGCHAIN_API_KEY"] == "ls-test"
