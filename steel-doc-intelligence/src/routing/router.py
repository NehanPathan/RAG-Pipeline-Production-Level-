from __future__ import annotations

from src.governance import feature_flags
from src.governance.safety import screen_question
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import retrieval_skipped, router_decisions
from src.monitoring.stage_tracer import traced_stage
from src.routing.classifier import RouteClassifier
from src.routing.routes import Route, RouteDecision, RouteSource
from src.routing.rules import apply_rules, mentions_documents, suggests_web_search
from src.tools.registry import tool_catalog

logger = get_logger(__name__)


def _keep_document_questions(query: str, decision: RouteDecision) -> RouteDecision:
    """Never answer a document question from the model's own knowledge.

    The classifier decides without knowing what the corpus contains, and it is
    confidently wrong in one particular direction: asked "What does the bent
    plate annotation refer to?" it returns `llm_knowledge` at 0.8 -- "a general
    concept that does not require internal documents" -- and the user gets a
    textbook paragraph about bending sheet metal while the drawing's own
    answer sits indexed and unread. The same question phrased as "what
    structural element does this annotation point to" routes to RAG, so the
    corpus was never the problem.

    The asymmetry justifies a deterministic override. Routing a general
    question to retrieval costs one wasted search and still answers correctly,
    because an empty retrieval refuses rather than confabulates. Routing a
    document question to model knowledge produces a fluent, uncited,
    unverifiable answer about somebody else's drawing -- which is the failure
    this whole system exists to prevent.

    `GREETING` is guarded for the same reason, and it became load-bearing once
    the greeting branch stopped answering everything with a bare "Hello.". A
    conversational reply now says what the corpus covers and that it cannot
    help with the question — useful when true, and a confident falsehood when
    the question was about a drawing all along. Sending it to retrieval instead
    costs one search and ends in a grounded answer or an honest refusal.

    Only ever moves *toward* retrieval, so it cannot weaken grounding.
    """
    if decision.route not in (Route.LLM_KNOWLEDGE, Route.WEB_SEARCH, Route.GREETING):
        return decision
    if decision.source is RouteSource.RULE:
        # A rule matched the whole utterance, anchored. "hi" is a greeting even
        # though "section" and "plan" are document words; only the classifier's
        # guesses are second-guessed here.
        return decision
    if not mentions_documents(query):
        return decision

    logger.info(
        "route_overridden_document_vocabulary",
        proposed=decision.route.value,
        confidence=decision.confidence,
    )
    return RouteDecision(
        route=Route.RAG,
        source=RouteSource.FALLBACK,
        confidence=decision.confidence,
        reason=(
            f"classifier proposed '{decision.route.value}', but the query uses "
            "document and drawing vocabulary, so it is answered from the corpus"
        ),
    )


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

        # Safety, before anything is spent. The output side already refuses an
        # ungrounded answer, so this is not the last line of defence -- it is
        # the cheap one, declining before a retrieval, a rerank, a compression
        # pass and a generation are paid for. Deterministic and narrow: see
        # `governance/safety.py` for why a blocklist of nouns would refuse the
        # drawings this system exists to read.
        verdict = screen_question(text)
        if verdict.refused:
            logger.warning("query_refused_by_safety", category=verdict.category)
            return RouteDecision(
                route=Route.REFUSE,
                source=RouteSource.RULE,
                confidence=1.0,
                reason=f"safety: {verdict.category}",
                args={"reply": verdict.reply},
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

        decision = await self._classifier.classify(text, tool_catalog())
        return self._respect_flags(_keep_document_questions(text, decision))

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
            logger.info("route_blocked_by_flag", route=decision.route.value, flag=gate)
            return RouteDecision(
                route=Route.RAG,
                source=RouteSource.FALLBACK,
                confidence=decision.confidence,
                reason=f"{decision.route.value} is disabled ({gate}); using document search",
            )
        return decision

    @staticmethod
    def _record(decision: RouteDecision) -> None:
        router_decisions.labels(route=decision.route.value, source=decision.source.value).inc()
        if not decision.route.needs_retrieval:
            # The metric that shows the router paying for itself: every
            # increment here is a vector search, a BM25 search, a rerank and
            # a compression call that did not happen.
            retrieval_skipped.labels(reason=decision.route.value).inc()
