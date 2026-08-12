from src.domain.value_objects.query_intent import IntentType, QueryIntent
from src.retrieval.agents.source_selector import SourceSelector


def test_select_returns_domain_when_specific():
    selector = SourceSelector()
    intent = QueryIntent(type=IntentType.POLICY_LOOKUP, confidence=0.9, domain="HR")

    assert selector.select(intent) == ["HR"]


def test_select_returns_empty_list_for_general_domain():
    selector = SourceSelector()
    intent = QueryIntent(type=IntentType.FACTUAL, confidence=0.5, domain="General")

    assert selector.select(intent) == []


def test_select_is_case_insensitive_for_general():
    selector = SourceSelector()
    intent = QueryIntent(type=IntentType.FACTUAL, confidence=0.5, domain="general")

    assert selector.select(intent) == []
