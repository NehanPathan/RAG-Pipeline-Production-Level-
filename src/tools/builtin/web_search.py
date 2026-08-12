from __future__ import annotations

import httpx

from src.config import get_settings
from src.monitoring.logger import get_logger
from src.tools.base import Tool, ToolError, ToolRequest, ToolResult
from src.tools.registry import tools

logger = get_logger(__name__)


class WebSearchTool(Tool):
    """Answers from live web results.

    Ships **disabled** (`enable_web_search=false`). Two reasons, both worth
    stating rather than discovering: it forwards the user's query verbatim to
    a third party, which is a data-egress decision someone should make
    deliberately; and it costs money per call, so an always-on default turns
    a routing bug into a bill.

    Results are never cached — a web answer that was right an hour ago may be
    wrong now, and serving it from cache would present staleness as certainty.
    """

    name = "web_search"
    description = (
        "Search the public web for current information: news, recent events, "
        "prices, or anything published after the indexed documents were "
        "written. Use only when the answer cannot come from internal documents."
    )
    requires_flag = "enable_web_search"
    is_external = True

    async def execute(self, request: ToolRequest) -> ToolResult:
        settings = get_settings()
        provider = settings.web_search_provider.strip().lower()

        if provider == "tavily":
            results = await _search_tavily(request.query, settings)
        elif provider == "serper":
            results = await _search_serper(request.query, settings)
        else:
            raise ToolError(
                f"Web search provider {provider!r} is not configured. "
                "Set WEB_SEARCH_PROVIDER to 'tavily' or 'serper'."
            )

        if not results:
            return ToolResult(
                answer="No relevant web results were found for that query.",
                data={"results": []},
                cacheable=False,
            )

        lines = [f"[{i}] {r['title']} — {r['snippet']}" for i, r in enumerate(results, start=1)]
        return ToolResult(
            answer="\n".join(lines),
            data={"results": results, "provider": provider},
            # Same shape the RAG path emits, so the UI renders web sources
            # through the existing citation component with no special casing.
            citations=[
                {
                    "index": i,
                    "source_name": r["title"],
                    "document_name": r["url"],
                    "chunk_id": None,
                    "document_id": None,
                    "page_number": None,
                    "section": None,
                }
                for i, r in enumerate(results, start=1)
            ],
            cacheable=False,
        )


async def _search_tavily(query: str, settings) -> list[dict]:
    if not settings.tavily_api_key:
        raise ToolError("TAVILY_API_KEY is not set.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "max_results": settings.web_search_max_results,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise ToolError(f"Web search request failed: {exc}") from exc

    return [
        {
            "title": item.get("title", "Untitled"),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:500],
        }
        for item in payload.get("results", [])[: settings.web_search_max_results]
    ]


async def _search_serper(query: str, settings) -> list[dict]:
    if not settings.serper_api_key:
        raise ToolError("SERPER_API_KEY is not set.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.serper_api_key},
                json={"q": query, "num": settings.web_search_max_results},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise ToolError(f"Web search request failed: {exc}") from exc

    return [
        {
            "title": item.get("title", "Untitled"),
            "url": item.get("link", ""),
            "snippet": (item.get("snippet") or "")[:500],
        }
        for item in payload.get("organic", [])[: settings.web_search_max_results]
    ]


@tools.register(
    "web_search",
    description=WebSearchTool.description,
    requires_flag=WebSearchTool.requires_flag,
    tags=("external", "network"),
)
def _build_web_search() -> Tool:
    return WebSearchTool()
