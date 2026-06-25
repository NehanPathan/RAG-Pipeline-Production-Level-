from __future__ import annotations

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

REWRITE_PROMPT = """\
Rewrite the following user query to be clear, grammatically correct, and \
fully self-contained, without changing its meaning or adding information \
that isn't implied by the original. Return ONLY the rewritten query text, \
with no extra commentary or quotes.

Query: {query}

Rewritten query:"""


class QueryRewriter:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def rewrite(self, query: str) -> str:
        async with traced_stage("query_rewrite", query=query) as stage:
            rewritten = await self._rewrite(query)
            stage.set_result(rewritten_query=rewritten)
            return rewritten

    async def _rewrite(self, query: str) -> str:
        try:
            response = await self._llm.complete(
                prompt=REWRITE_PROMPT.format(query=query), max_tokens=200, temperature=0.0
            )
            rewritten = response.strip().strip('"')
            return rewritten or query
        except Exception as e:
            logger.warning("query_rewrite_failed", query=query[:100], error=str(e))
            return query
