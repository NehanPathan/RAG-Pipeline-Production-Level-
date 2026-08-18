from __future__ import annotations

from src.domain.value_objects.query_intent import IntentType, QueryIntent
from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

CLASSIFY_PROMPT = """\
Classify the intent of the following user query for a document retrieval system.

Return ONLY valid JSON with these fields:
- intent: one of [factual, policy_lookup, analytical, comparison, summarization, conversational, out_of_scope]
- confidence: float between 0.0 and 1.0
- domain: one of [HR, Legal, Finance, Operations, Engineering, Sales, Marketing, General]

Query: {query}

JSON response:"""


class IntentClassifier:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def classify(self, query: str) -> QueryIntent:
        async with traced_stage("intent_classification", query=query) as stage:
            intent = await self._classify(query)
            stage.set_result(
                intent=intent.type.value, confidence=intent.confidence, domain=intent.domain
            )
            return intent

    async def _classify(self, query: str) -> QueryIntent:
        try:
            response = await self._llm.complete(
                prompt=CLASSIFY_PROMPT.format(query=query), max_tokens=150, temperature=0.0
            )
            data = parse_json_response(response)
            if not isinstance(data, dict):
                raise ValueError(f"expected JSON object, got {type(data).__name__}")

            try:
                intent_type = IntentType(str(data.get("intent", "")).lower())
            except ValueError:
                intent_type = IntentType.FACTUAL

            return QueryIntent(
                type=intent_type,
                confidence=float(data.get("confidence", 0.0) or 0.0),
                domain=str(data.get("domain") or "General"),
            )
        except Exception as e:
            logger.warning("intent_classification_failed", query=query[:100], error=str(e))
            return QueryIntent(type=IntentType.FACTUAL, confidence=0.0, domain="General")
