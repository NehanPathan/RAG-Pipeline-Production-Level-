from __future__ import annotations

import pytest

from src.routing.routes import Route, RouteSource
from src.routing.rules import apply_rules, mentions_documents, suggests_web_search


class TestGreetingRules:
    @pytest.mark.parametrize(
        "query", ["hi", "Hello!", "hey", "good morning", "how are you?", "  yo  "]
    )
    def test_greetings_route_without_any_model_call(self, query):
        decision = apply_rules(query)
        assert decision is not None
        assert decision.route is Route.GREETING
        assert decision.source is RouteSource.RULE
        assert decision.args["reply"]

    @pytest.mark.parametrize("query", ["thanks", "thank you", "bye", "ok", "got it"])
    def test_acknowledgements_are_greetings(self, query):
        assert apply_rules(query).route is Route.GREETING

    @pytest.mark.parametrize(
        "query",
        [
            "hi, what is our refund policy?",
            "which hire policy applies to contractors",
            "hello world program documentation",
        ],
    )
    def test_greeting_words_inside_real_questions_do_not_match(self, query):
        """The anchors matter: an unanchored greeting pattern would hijack
        every question containing 'hi' or 'hello'."""
        decision = apply_rules(query)
        assert decision is None or decision.route is not Route.GREETING


class TestArithmeticRule:
    @pytest.mark.parametrize(
        "query", ["12 * 7", "what is 45 + 55?", "calculate 100 / 4", "2^10", "sqrt(144) + 3"]
    )
    def test_arithmetic_routes_to_calculator(self, query):
        assert apply_rules(query).route is Route.CALCULATOR

    @pytest.mark.parametrize(
        "query",
        [
            "compare Q1 and Q2 revenue",
            "what is section 3.2 about",
            "how many days of leave do I get",
            "summarise the 2024 report",
        ],
    )
    def test_prose_with_digits_is_not_arithmetic(self, query):
        """A false positive here answers a real question with an arithmetic
        error, which is far worse than a needless retrieval."""
        decision = apply_rules(query)
        assert decision is None or decision.route is not Route.CALCULATOR


class TestDateTimeRule:
    @pytest.mark.parametrize(
        "query", ["what is the date", "what time is it", "today's date", "what day is it"]
    )
    def test_datetime_questions_route_to_the_clock(self, query):
        assert apply_rules(query).route is Route.DATETIME

    def test_document_question_about_dates_is_not_the_clock(self):
        decision = apply_rules("what is the effective date of the contract")
        assert decision is None or decision.route is not Route.DATETIME


class TestTranslationRule:
    @pytest.mark.parametrize(
        "query",
        [
            "translate hello into Spanish",
            "translate 'good morning' to French",
            "how do you say thank you in Japanese",
        ],
    )
    def test_translation_requests_are_recognised(self, query):
        decision = apply_rules(query)
        assert decision.route is Route.TRANSLATION
        assert decision.args["text"]
        assert decision.args["language"]

    def test_non_translation_falls_through(self):
        assert apply_rules("what does the policy say about translation services") is None


class TestFallthrough:
    @pytest.mark.parametrize(
        "query",
        [
            "what is our refund policy",
            "summarise the Q3 engineering report",
            "explain the difference between the two contracts",
        ],
    )
    def test_document_questions_fall_through_to_the_classifier(self, query):
        assert apply_rules(query) is None

    def test_empty_query_falls_through(self):
        assert apply_rules("") is None
        assert apply_rules("   ") is None


class TestWebSearchHeuristic:
    @pytest.mark.parametrize(
        "query", ["what is the latest news on AI regulation", "current weather in Paris"]
    )
    def test_recency_signals_suggest_web_search(self, query):
        assert suggests_web_search(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "what does our latest policy say about remote work",
            "the current version of the employee handbook",
        ],
    )
    def test_document_signal_beats_recency_signal(self, query):
        """'latest' plus 'policy' is a document question. Getting this
        backwards answers an internal question from a public source."""
        assert mentions_documents(query) is True
        assert suggests_web_search(query) is False

    def test_plain_question_suggests_nothing(self):
        assert suggests_web_search("how do I request leave") is False
