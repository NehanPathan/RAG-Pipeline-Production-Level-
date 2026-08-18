from __future__ import annotations

from datetime import datetime

from src.tools.base import ToolRequest
from src.tools.builtin.datetime_tool import DateTimeTool


async def _run(**args):
    return await DateTimeTool().execute(ToolRequest(query="what time is it", args=args))


class TestDateTimeTool:
    async def test_answers_from_the_clock_rather_than_a_model(self):
        """An LLM asked the current date answers confidently and wrongly from
        its training cutoff."""
        result = await _run()

        assert result.succeeded is True
        assert datetime.fromisoformat(result.data["iso"]).year >= 2024

    async def test_defaults_to_utc(self):
        result = await _run()

        assert result.data["timezone"] == "UTC"
        assert "(UTC)" in result.answer

    async def test_honours_a_requested_timezone(self):
        result = await _run(timezone="Europe/Paris")

        assert result.data["timezone"] == "Europe/Paris"
        assert "(Europe/Paris)" in result.answer

    async def test_an_unknown_timezone_falls_back_to_utc(self):
        result = await _run(timezone="Mars/Olympus_Mons")

        assert result.data["timezone"] == "UTC"

    async def test_a_malformed_timezone_falls_back_to_utc(self):
        result = await _run(timezone="../../etc/passwd")

        assert result.data["timezone"] == "UTC"

    async def test_the_answer_is_never_cacheable(self):
        """Caching "what time is it" serves a stale answer with total
        confidence -- the whole point is that it changes."""
        assert (await _run()).cacheable is False

    async def test_reports_an_epoch_alongside_the_prose(self):
        result = await _run()

        assert isinstance(result.data["epoch"], float)
