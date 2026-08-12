from __future__ import annotations

from src.domain.value_objects.query_intent import QueryIntent

_NO_RESTRICTION_DOMAIN = "general"


class SourceSelector:
    """Decides which document domain(s) to restrict retrieval to.

    Deliberately deterministic, not LLM-based: the domain is already produced
    by IntentClassifier as a side effect of one LLM call, so a second LLM
    round-trip here would just be redundant latency/cost for the same
    decision. If the platform later needs to route across multiple Qdrant
    collections / ES indices (e.g. per-tenant or per-department), this is the
    seam to extend — today there's a single shared index, so "selection"
    narrows the domain payload filter rather than picking a different index.
    """

    def select(self, intent: QueryIntent) -> list[str]:
        if intent.domain.lower() == _NO_RESTRICTION_DOMAIN:
            return []
        return [intent.domain]
