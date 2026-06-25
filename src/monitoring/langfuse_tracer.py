from __future__ import annotations

from langfuse import Langfuse

_langfuse_client: Langfuse | None = None


def get_langfuse_client() -> Langfuse:
    """Singleton Langfuse client.

    Gracefully no-ops (logs an auth warning once, exports nothing) when
    public/secret keys are not configured, so it is safe to call
    unconditionally in dev/test environments without branching on
    "is langfuse configured" at every call site.
    """
    global _langfuse_client
    if _langfuse_client is None:
        from src.config import get_settings

        settings = get_settings()
        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key or None,
            secret_key=settings.langfuse_secret_key or None,
            host=settings.langfuse_host,
        )
    return _langfuse_client


def reset_langfuse_client() -> None:
    """Test helper: force re-creation of the singleton on next access."""
    global _langfuse_client
    _langfuse_client = None
