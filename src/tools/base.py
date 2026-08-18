from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.governance.rbac import Principal
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import tool_invocations, tool_latency
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)


@dataclass
class ToolRequest:
    """What a tool is asked to do.

    Carries the `principal` because several tools make authorization
    decisions of their own — the SQL tool needs to know who is asking, and a
    tool that cannot see the caller cannot enforce anything.
    """

    query: str
    principal: Principal | None = None
    args: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class ToolResult:
    """What a tool returns.

    `answer` is user-facing text; `data` is the structured result for a
    caller that wants to post-process; `citations` follow the same shape the
    RAG path produces, so an answer sourced from a tool is displayed by the
    existing UI with no special casing.
    """

    answer: str
    data: dict[str, Any] = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    succeeded: bool = True
    error: str = ""
    # Whether this answer may be stored in the semantic cache. False for
    # anything time-varying: caching "what time is it" or a live web result
    # would serve a stale answer with total confidence.
    cacheable: bool = True


class ToolError(RuntimeError):
    """A tool failed in a way the caller should surface, not retry."""


class Tool(ABC):
    """One capability the router can dispatch to.

    Subclasses implement `execute`; `run` wraps it with the tracing, metrics
    and error handling every tool needs, so no tool has to remember them and
    none can report inconsistently.
    """

    #: Stable identifier the router selects by. Must match the registry name.
    name: str = ""
    #: Shown to the classifier LLM — this is what makes the tool selectable,
    #: so it should describe *when to use it*, not how it works.
    description: str = ""
    #: Feature flag gating this tool, or None if always available.
    requires_flag: str | None = None
    #: Tools that can reach outside the deployment or mutate state need a
    #: reviewed decision before use; the router logs them differently.
    is_external: bool = False

    @abstractmethod
    async def execute(self, request: ToolRequest) -> ToolResult:
        """Do the work. Raise ToolError for expected failures."""

    async def run(self, request: ToolRequest) -> ToolResult:
        start = time.perf_counter()
        async with traced_stage(
            f"tool:{self.name}", query=request.query[:200], external=self.is_external
        ) as stage:
            try:
                result = await self.execute(request)
            except ToolError as exc:
                tool_invocations.labels(tool=self.name, outcome="error").inc()
                logger.warning("tool_failed", tool=self.name, error=str(exc))
                stage.set_result(succeeded=False, error=str(exc))
                return ToolResult(
                    answer="", succeeded=False, error=str(exc), cacheable=False
                )
            except Exception as exc:
                # An unexpected exception is a defect in the tool, not a
                # user error; it is logged at error level and still contained
                # so one broken tool cannot take down the request path.
                tool_invocations.labels(tool=self.name, outcome="error").inc()
                logger.error("tool_crashed", tool=self.name, error=str(exc), exc_info=True)
                stage.set_result(succeeded=False, error=str(exc))
                return ToolResult(
                    answer="",
                    succeeded=False,
                    error=f"{self.name} failed unexpectedly.",
                    cacheable=False,
                )
            finally:
                tool_latency.labels(tool=self.name).observe(time.perf_counter() - start)

            tool_invocations.labels(tool=self.name, outcome="ok").inc()
            stage.set_result(succeeded=True, answer_chars=len(result.answer))
            return result

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "requires_flag": self.requires_flag,
            "is_external": self.is_external,
        }
