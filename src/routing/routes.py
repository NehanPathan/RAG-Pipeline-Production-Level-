from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Route(str, Enum):
    """Where a query should be answered from.

    Ordered roughly by cost, cheapest first — which is also the order the
    rule layer tries them in.
    """

    GREETING = "greeting"
    CALCULATOR = "calculator"
    DATETIME = "datetime"
    TRANSLATION = "translation"
    LLM_KNOWLEDGE = "llm_knowledge"
    WEB_SEARCH = "web_search"
    SQL = "sql"
    RAG = "rag"
    REFUSE = "refuse"

    @property
    def needs_retrieval(self) -> bool:
        return self is Route.RAG

    @property
    def needs_llm(self) -> bool:
        """Routes that cost at least one model call.

        The three that don't — greeting, calculator, datetime — are the
        reason the router pays for itself.
        """
        return self in {
            Route.TRANSLATION,
            Route.LLM_KNOWLEDGE,
            Route.WEB_SEARCH,
            Route.RAG,
        }

    @property
    def tool_name(self) -> str | None:
        return {
            Route.CALCULATOR: "calculator",
            Route.DATETIME: "datetime",
            Route.TRANSLATION: "translation",
            Route.WEB_SEARCH: "web_search",
            Route.SQL: "sql_query",
        }.get(self)


class RouteSource(str, Enum):
    """How the decision was reached — recorded on every routing metric.

    Worth distinguishing: a rising share of `llm` decisions means the rule
    layer is losing coverage and costing a classifier call per query, while a
    rising share of `fallback` means the classifier is returning something
    unusable.
    """

    RULE = "rule"
    LLM = "llm"
    FALLBACK = "fallback"
    FORCED = "forced"


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    source: RouteSource
    confidence: float = 1.0
    reason: str = ""
    #: Arguments extracted during classification and handed to the tool, so a
    #: tool never has to re-parse the query the router already understood.
    args: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "route": self.route.value,
            "source": self.source.value,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }
