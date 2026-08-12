from __future__ import annotations

from src.governance import feature_flags
from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage
from src.routing.routes import Route, RouteDecision, RouteSource

logger = get_logger(__name__)

CLASSIFY_PROMPT = """You route user queries in a document-question-answering system.

Choose exactly one route:

- rag: the answer is in the organisation's indexed documents (policies, contracts,
  handbooks, reports, procedures). This is the default for anything about
  "our", "the company", or any internal subject matter.
- llm_knowledge: general knowledge, definitions, explanations, writing help or
  reasoning that needs no documents and no current data.
- web_search: needs information published after the documents were written —
  news, current prices, weather, recent events.
- sql: a question about structured records, counts or aggregates in a database.
- calculator: a self-contained arithmetic or mathematical expression.
- translation: a request to translate text between languages.
- greeting: a greeting, thanks, or a question about your own capabilities.

Rules:
- If the query could plausibly be answered from internal documents, choose rag.
- Only choose web_search when the answer must be newer than the documents.
- confidence is your genuine certainty from 0.0 to 1.0. Use a low value when
  the query is ambiguous; the system falls back to document search when you do.

Available tools: {tools}

Return ONLY valid JSON:
{{"route": "<route>", "confidence": <float>, "reason": "<short reason>"}}

Query: {query}

JSON:"""


class RouteClassifier:
    """LLM fallback for queries no deterministic rule recognised.

    Uses the small model: this call happens before the real work, so an
    expensive classifier would eat the savings routing is meant to produce.
    """

    def __init__(self, llm_provider: LLMProvider, min_confidence: float = 0.6) -> None:
        self._llm = llm_provider
        self._min_confidence = min_confidence

    async def classify(self, query: str, tool_catalog: str = "") -> RouteDecision:
        async with traced_stage("route_classification", query=query[:200]) as stage:
            decision = await self._classify(query, tool_catalog)
            stage.set_result(**decision.as_dict())
            return decision

    async def _classify(self, query: str, tool_catalog: str) -> RouteDecision:
        try:
            response = await self._llm.complete(
                CLASSIFY_PROMPT.format(query=query, tools=tool_catalog or "(none)"),
                max_tokens=150,
                temperature=0.0,
            )
            data = parse_json_response(response)
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")

            route = Route(str(data.get("route", "")).strip().lower())
            confidence = float(data.get("confidence", 0.0) or 0.0)
            reason = str(data.get("reason", ""))[:200]
        except ValueError as exc:
            # Unknown route name or unparseable confidence. Falling back to
            # RAG is the safe direction: a needless retrieval costs money,
            # answering from the wrong source costs correctness.
            logger.warning("route_classification_unusable", query=query[:100], error=str(exc))
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FALLBACK,
                confidence=0.0,
                reason="classifier returned an unrecognised route",
            )
        except Exception as exc:
            logger.warning("route_classification_failed", query=query[:100], error=str(exc))
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FALLBACK,
                confidence=0.0,
                reason=f"classifier unavailable: {exc}",
            )

        if confidence < self._min_confidence:
            logger.info(
                "route_confidence_below_threshold",
                proposed=route.value,
                confidence=confidence,
                threshold=self._min_confidence,
            )
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FALLBACK,
                confidence=confidence,
                reason=(
                    f"classifier proposed '{route.value}' at {confidence:.2f}, "
                    f"below the {self._min_confidence:.2f} threshold"
                ),
            )

        route = _downgrade_if_disabled(route)
        return RouteDecision(
            route=route, source=RouteSource.LLM, confidence=confidence, reason=reason
        )


def _downgrade_if_disabled(route: Route) -> Route:
    """Send a query to RAG when its chosen route is switched off.

    The classifier is told which tools exist but cannot be trusted to respect
    that — models pick plausible-sounding options regardless. Enforcing it
    here means turning off `enable_web_search` genuinely stops web traffic,
    rather than merely discouraging it.
    """
    gate = {
        Route.WEB_SEARCH: "enable_web_search",
        Route.SQL: "enable_sql_tool",
        Route.CALCULATOR: "enable_calculator",
    }.get(route)

    if gate and not feature_flags.is_enabled(gate):
        logger.info("route_downgraded_disabled_capability", route=route.value, flag=gate)
        return Route.RAG
    return route
