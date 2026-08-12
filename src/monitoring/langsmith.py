"""LangSmith wiring — evaluation only, tracing off unless explicitly enabled.

This module exists because LangChain enables LangSmith tracing *from the
environment*: if `LANGSMITH_API_KEY` (or the older `LANGCHAIN_API_KEY`) is
present and `LANGSMITH_TRACING` is truthy, every chain, model and retriever
call is exported to LangChain's cloud automatically, with no code opting in.

For this system that would send prompt text and retrieved document content
-- which is to say the client's structural drawings -- to a third party, as
a side effect of an environment variable someone set to run an evaluation.
Runtime tracing is Langfuse's job here precisely because it can be
self-hosted.

So the flags are set explicitly at startup, in both directions: enabled only
when `LANGSMITH_TRACING=true` is deliberately configured, and actively
disabled otherwise rather than left to whatever happens to be in the
environment. Evaluation does not depend on this -- the LangSmith SDK is used
directly there and needs only the API key.
"""

from __future__ import annotations

import os

from src.config import Settings
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# The modern names, plus the legacy `LANGCHAIN_*` aliases that older
# langchain-core builds still honour. Both are set, so a stale alias in a
# deployment's environment cannot re-enable export behind our backs.
_TRACING_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
_ENDPOINT_VARS = ("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")
_PROJECT_VARS = ("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT")
_API_KEY_VARS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")


def configure_langsmith(settings: Settings) -> bool:
    """Apply the tracing decision to the process environment.

    Returns whether tracing ended up enabled, so startup can log the fact --
    "traces are leaving the network" should be visible in the logs, not
    inferred from configuration.
    """
    enabled = bool(settings.langsmith_tracing and settings.langsmith_api_key)

    if not enabled:
        for name in _TRACING_VARS:
            os.environ[name] = "false"
        if settings.langsmith_tracing and not settings.langsmith_api_key:
            logger.warning("langsmith_tracing_requested_without_api_key")
        return False

    for name in _TRACING_VARS:
        os.environ[name] = "true"
    for name in _API_KEY_VARS:
        os.environ[name] = settings.langsmith_api_key
    for name in _ENDPOINT_VARS:
        os.environ[name] = settings.langsmith_endpoint
    for name in _PROJECT_VARS:
        os.environ[name] = settings.langsmith_project

    logger.warning(
        "langsmith_tracing_enabled",
        project=settings.langsmith_project,
        endpoint=settings.langsmith_endpoint,
        note="prompt text and retrieved document content are exported to LangSmith",
    )
    return True
