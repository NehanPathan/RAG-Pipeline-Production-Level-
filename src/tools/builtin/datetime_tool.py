from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.tools.base import Tool, ToolRequest, ToolResult
from src.tools.registry import tools


class DateTimeTool(Tool):
    """Answers "what is the date/time" without a model call.

    Small, but it fixes a real failure: an LLM asked the current date answers
    confidently and wrongly from its training cutoff. Routing this to a clock
    is both cheaper and the only way to be correct.
    """

    name = "datetime"
    description = (
        "Report the current date, time, day of week or timezone. Use for any "
        "question about what time or date it is now."
    )

    async def execute(self, request: ToolRequest) -> ToolResult:
        tz_name = str(request.args.get("timezone") or "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz, tz_name = timezone.utc, "UTC"

        now = datetime.now(tz)
        return ToolResult(
            answer=now.strftime(f"%A, %d %B %Y, %H:%M:%S ({tz_name})"),
            data={"iso": now.isoformat(), "timezone": tz_name, "epoch": now.timestamp()},
            # Never cacheable: the whole point is that it changes.
            cacheable=False,
        )


@tools.register(
    "datetime",
    description=DateTimeTool.description,
    tags=("utility", "offline"),
)
def _build_datetime() -> Tool:
    return DateTimeTool()
