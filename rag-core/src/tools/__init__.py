"""Tool layer — capabilities the router can dispatch to.

Replaces the if/elif chain a growing set of capabilities would otherwise
become. Every tool declares its own name, description, schema and required
feature flag; the router picks one by name and never knows what it does.

    from src.tools import tools, load_builtin_tools

    load_builtin_tools()
    result = await tools.create("calculator").run(ToolRequest(query="12 * 7"))

Security posture, stated once because it governs every tool here: tool input
originates from user text that has usually passed through an LLM, so it is
untrusted twice over. Tools therefore validate their own input rather than
trusting the router, the calculator parses an AST instead of calling eval(),
and the SQL tool runs read-only against an explicit table allow-list. Tools
that widen the blast radius (web search, SQL) ship disabled.
"""

from src.tools.base import Tool, ToolError, ToolRequest, ToolResult
from src.tools.registry import load_builtin_tools, tools

__all__ = [
    "Tool",
    "ToolError",
    "ToolRequest",
    "ToolResult",
    "load_builtin_tools",
    "tools",
]
