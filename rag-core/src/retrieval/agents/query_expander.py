from __future__ import annotations

from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

EXPAND_PROMPT = """\
Generate {count} alternative phrasings or closely related search queries for \
the following query, to widen retrieval recall. Each variant should express \
the same information need using different words.

Return ONLY a valid JSON list of strings, e.g. ["variant one", "variant two"].

Query: {query}

JSON response:"""


class QueryExpander:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def expand(self, query: str, count: int = 3) -> list[str]:
        async with traced_stage("query_expansion", query=query, count=count) as stage:
            variants = await self._expand(query, count)
            stage.set_result(variants_generated=len(variants))
            return variants

    async def _expand(self, query: str, count: int) -> list[str]:
        try:
            response = await self._llm.complete(
                prompt=EXPAND_PROMPT.format(query=query, count=count),
                max_tokens=300,
                temperature=0.4,
            )
            data = parse_json_response(response)
            if not isinstance(data, list):
                return []
            return [str(item).strip() for item in data if str(item).strip()][:count]
        except Exception as e:
            logger.warning("query_expansion_failed", query=query[:100], error=str(e))
            return []
