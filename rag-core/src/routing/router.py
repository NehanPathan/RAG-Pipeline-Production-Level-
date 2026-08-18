from __future__ import annotations

from src.governance import feature_flags
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import retrieval_skipped, router_decisions
from src.monitoring.stage_tracer import traced_stage
from src.routing.classifier import RouteClassifier
from src.routing.routes import Route, RouteDecision, RouteSource
from src.routing.rules import apply_rules, suggests_web_search
from src.tools.registry import tool_catalog

logger = get_logger(__name__)


class QueryRouter:
    """Decides how a query should be answered, before any work is done.

    Three layers, cheapest first:

      1. **Rules** — deterministic patterns (greeting, arithmetic, date,
         translation). Free, instant, reproducible, and they cover the bulk
         of the traffic that does not need retrieval.
      2. **Heuristics** — a recency signal that suggests web search, gated by
         whether the query also looks document-related.
      3. **Classifier** — one small-model call for whatever is left.

    Disabling `enable_query_router` makes every query take the RAG route,
    restoring the previous behaviour exactly. That is deliberate: a routing
    bug should be recoverable by flipping one flag, not by a rollback.
    """

    def __init__(self, classifier: RouteClassifier, rules_only: bool = False) -> None:
        self._classifier = classifier
        self._rules_only = rules_only

    async def route(self, query: str) -> RouteDecision:
        async with traced_stage("query_routing", query=(query or "")[:200]) as stage:
            decision = await self._decide(query)
            self._record(decision)
            stage.set_result(**decision.as_dict())
            return decision

    async def _decide(self, query: str) -> RouteDecision:
        if not feature_flags.is_enabled("enable_query_router"):
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FORCED,
                confidence=1.0,
                reason="query router disabled by feature flag",
            )

        text = (query or "").strip()
        if not text:
            return RouteDecision(
                route=Route.REFUSE,
                source=RouteSource.RULE,
                confidence=1.0,
                reason="empty query",
            )

        rule_decision = apply_rules(text)
        if rule_decision is not None:
            return self._respect_flags(rule_decision)

        if suggests_web_search(text) and feature_flags.is_enabled("enable_web_search"):
            return RouteDecision(
                route=Route.WEB_SEARCH,
                source=RouteSource.RULE,
                confidence=0.8,
                reason="query asks for current information and mentions no documents",
            )

        if self._rules_only:
            # Rules-only mode trades routing precision for a guaranteed zero
            # classifier cost. Everything unmatched goes to RAG, which is the
            # pre-router behaviour.
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FORCED,
                confidence=1.0,
                reason="no rule matched and classifier is disabled (rules-only mode)",
            )

        return self._respect_flags(await self._classifier.classify(text, tool_catalog()))

    @staticmethod
    def _respect_flags(decision: RouteDecision) -> RouteDecision:
        """Final gate: never return a route whose capability is switched off.

        Applied to rule decisions as well as classifier ones — a rule is just
        as capable of selecting a disabled tool, and the flag has to mean the
        same thing regardless of how the route was chosen.
        """
        gate = {
            Route.CALCULATOR: "enable_calculator",
            Route.WEB_SEARCH: "enable_web_search",
            Route.SQL: "enable_sql_tool",
        }.get(decision.route)

        if gate and not feature_flags.is_enabled(gate):
            logger.info(
                "route_blocked_by_flag", route=decision.route.value, flag=gate
            )
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FALLBACK,
                confidence=decision.confidence,
                reason=f"{decision.route.value} is disabled ({gate}); using document search",
            )
        return decision

    @staticmethod
    def _record(decision: RouteDecision) -> None:
        router_decisions.labels(
            route=decision.route.value, source=decision.source.value
        ).inc()
        if not decision.route.needs_retrieval:
            # The metric that shows the router paying for itself: every
            # increment here is a vector search, a BM25 search, a rerank and
            # a compression call that did not happen.
            retrieval_skipped.labels(reason=decision.route.value).inc()
