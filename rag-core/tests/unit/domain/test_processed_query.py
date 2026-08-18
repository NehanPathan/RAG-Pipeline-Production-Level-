from src.domain.value_objects.processed_query import ProcessedQuery
from src.domain.value_objects.query_intent import IntentType


def test_all_queries_includes_rewritten_and_expansions():
    pq = ProcessedQuery(
        original_query="orig",
        rewritten_query="rewritten",
        expanded_queries=["variant a", "variant b"],
    )

    assert pq.all_queries == ["rewritten", "variant a", "variant b"]


def test_all_queries_deduplicates_expansion_matching_rewritten():
    pq = ProcessedQuery(
        original_query="orig",
        rewritten_query="same text",
        expanded_queries=["same text", "different"],
    )

    assert pq.all_queries == ["same text", "different"]


def test_default_intent_is_factual():
    pq = ProcessedQuery(original_query="x", rewritten_query="x")

    assert pq.intent.type == IntentType.FACTUAL
