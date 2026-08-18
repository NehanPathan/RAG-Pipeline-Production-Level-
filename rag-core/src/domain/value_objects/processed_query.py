from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.value_objects.metadata_filter import MetadataFilterSpec
from src.domain.value_objects.query_intent import IntentType, QueryIntent


@dataclass
class ProcessedQuery:
    """Full output of the Query Intelligence Layer (Module A)."""

    original_query: str
    rewritten_query: str
    expanded_queries: list[str] = field(default_factory=list)
    intent: QueryIntent = field(default_factory=lambda: QueryIntent(type=IntentType.FACTUAL))
    selected_sources: list[str] = field(default_factory=list)
    filters: MetadataFilterSpec = field(default_factory=MetadataFilterSpec)

    @property
    def all_queries(self) -> list[str]:
        """Rewritten query plus expansions, deduplicated, for fan-out to retrieval."""
        seen = {self.rewritten_query}
        result = [self.rewritten_query]
        for q in self.expanded_queries:
            if q not in seen:
                seen.add(q)
                result.append(q)
        return result
