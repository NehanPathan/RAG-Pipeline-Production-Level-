from __future__ import annotations

from src.monitoring.logger import get_logger
from src.plugins import PluginRegistry
from src.plugins.registry import PluginDisabledError
from src.tools.base import Tool

logger = get_logger(__name__)

tools: PluginRegistry[Tool] = PluginRegistry("tool")

_loaded = False


def load_builtin_tools(strict: bool = False) -> None:
    """Import the built-in tool modules so their registrations run.

    Idempotent. Called at API startup and from the router's first use, so a
    script that touches the router without going through FastAPI still gets
    a populated registry rather than a confusing "no tool registered" error.
    """
    global _loaded
    if _loaded:
        return

    from src.plugins.discovery import load_package

    load_package("src.tools.builtin", strict=strict)
    _loaded = True
    logger.info("tools_loaded", count=len(tools), names=tools.names())


def reset_tools() -> None:
    """Test helper: allow the built-in modules to be re-imported."""
    global _loaded
    _loaded = False


def available_tools() -> list[Tool]:
    """Every registered tool whose feature flag is currently enabled.

    Disabled tools are skipped rather than raising, because this feeds the
    classifier's menu: offering a tool that cannot run would let the router
    pick it and then fail.
    """
    load_builtin_tools()
    result: list[Tool] = []
    for spec in tools.specs():
        try:
            result.append(tools.create(spec.name))
        except PluginDisabledError:
            continue
        except Exception as exc:
            logger.warning("tool_construction_failed", tool=spec.name, error=str(exc))
    return result


def get_tool(name: str) -> Tool:
    load_builtin_tools()
    return tools.create(name)


def tool_catalog() -> str:
    """The tool menu given to the classifier LLM.

    Built from the live registry rather than a hardcoded prompt string, so a
    newly added tool becomes selectable the moment it registers — the
    alternative is a prompt that silently drifts out of date and a tool that
    is installed but never chosen.
    """
    lines = [f"- {tool.name}: {tool.description}" for tool in available_tools()]
    return "\n".join(lines) if lines else "(no tools available)"
