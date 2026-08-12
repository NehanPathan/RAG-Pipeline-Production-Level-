from __future__ import annotations

import asyncio
import uuid

from src.domain.value_objects.processed_query import ProcessedQuery
from src.monitoring.stage_tracer import traced_stage
from src.retrieval.agents.filter_generator import FilterGenerator
from src.retrieval.agents.intent_classifier import IntentClassifier
from src.retrieval.agents.query_expander import QueryExpander
from src.retrieval.agents.query_rewriter import QueryRewriter
from src.retrieval.agents.source_selector import SourceSelector


class QueryAgent:
    """Orchestrates the full Query Intelligence Layer (Module A).

    rewrite -> {expand, classify} in parallel -> select sources -> generate filters
    """

    def __init__(
        self,
        rewriter: QueryRewriter,
        expander: QueryExpander,
        classifier: IntentClassifier,
        source_selector: SourceSelector,
        filter_generator: FilterGenerator,
        expansion_count: int = 3,
    ) -> None:
        self._rewriter = rewriter
        self._expander = expander
        self._classifier = classifier
        self._source_selector = source_selector
        self._filter_generator = filter_generator
        self._expansion_count = expansion_count

    async def process(self, query: str, user_id: uuid.UUID | None = None) -> ProcessedQuery:
        async with traced_stage("query_intelligence", query=query) as stage:
            rewritten = await self._rewriter.rewrite(query)

            expanded, intent = await asyncio.gather(
                self._expander.expand(rewritten, count=self._expansion_count),
                self._classifier.classify(rewritten),
            )

            selected_sources = self._source_selector.select(intent)
            filters = await self._filter_generator.generate(
                query=rewritten,
                intent=intent,
                selected_sources=selected_sources,
                user_id=user_id,
            )

            processed = ProcessedQuery(
                original_query=query,
                rewritten_query=rewritten,
                expanded_queries=expanded,
                intent=intent,
                selected_sources=selected_sources,
                filters=filters,
            )
            stage.set_result(
                intent=intent.type.value,
                domain=intent.domain,
                expanded_count=len(expanded),
            )
            return processed
