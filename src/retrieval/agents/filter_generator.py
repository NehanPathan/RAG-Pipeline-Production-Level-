from __future__ import annotations

import uuid
from datetime import date, timedelta

from src.domain.value_objects.metadata_filter import MetadataFilterSpec
from src.domain.value_objects.query_intent import QueryIntent
from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

FILTER_PROMPT = """\
Extract structured search constraints implied by the following query.

Return ONLY valid JSON with these fields:
- tags: list of relevant keyword tags implied by the query (lowercase, empty list if none)
- file_type: one of [pdf, docx, txt, md, html] if the query implies a specific document format, else null
- recency_days: integer number of days if the query implies "recent"/"latest"/"this year" etc, else null

Query: {query}

JSON response:"""


class FilterGenerator:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def generate(
        self,
        query: str,
        intent: QueryIntent,
        selected_sources: list[str],
        user_id: uuid.UUID | None = None,
    ) -> MetadataFilterSpec:
        async with traced_stage("filter_generation", query=query) as stage:
            spec = await self._generate(query, selected_sources, user_id)
            stage.set_result(domain=spec.domain, tags=spec.tags, file_type=spec.file_type)
            return spec

    async def _generate(
        self,
        query: str,
        selected_sources: list[str],
        user_id: uuid.UUID | None,
    ) -> MetadataFilterSpec:
        domain = selected_sources[0] if selected_sources else None
        fallback = MetadataFilterSpec(user_id=user_id, domain=domain)

        try:
            response = await self._llm.complete(
                prompt=FILTER_PROMPT.format(query=query), max_tokens=200, temperature=0.0
            )
            data = parse_json_response(response)
            if not isinstance(data, dict):
                return fallback

            tags = [str(t).strip().lower() for t in data.get("tags") or [] if str(t).strip()] or None
            file_type = data.get("file_type") or None
            recency_days = data.get("recency_days")
            date_from = date.today() - timedelta(days=int(recency_days)) if recency_days else None

            return MetadataFilterSpec(
                user_id=user_id,
                domain=domain,
                tags=tags,
                file_type=file_type,
                date_from=date_from,
            )
        except Exception as e:
            logger.warning("filter_generation_failed", query=query[:100], error=str(e))
            return fallback
