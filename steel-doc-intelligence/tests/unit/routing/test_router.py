"""The router, and specifically the one direction it must never be wrong in.

Routing a general question to retrieval wastes a search and still answers
correctly, because an empty retrieval refuses. Routing a *document* question
to model knowledge produces a fluent, uncited paragraph about somebody else's
drawing. The tests below pin that asymmetry.
"""

from __future__ import annotations

import pytest

from src.routing.classifier import RouteClassifier
from src.routing.router import QueryRouter
from src.routing.routes import Route, RouteDecision, RouteSource


class _FixedClassifier(RouteClassifier):
    """Returns a decision chosen by the test, with no model call."""

    def __init__(self, route: Route, confidence: float = 0.8) -> None:
        self._route = route
        self._confidence = confidence
        self.calls = 0

    async def classify(self, query: str, tool_catalog: str = "") -> RouteDecision:
        self.calls += 1
        return RouteDecision(
            route=self._route,
            source=RouteSource.LLM,
            confidence=self._confidence,
            reason="fixed for the test",
        )


@pytest.mark.asyncio
class TestDocumentQuestionsReachRetrieval:
    @pytest.mark.parametrize(
        "query",
        [
            "What does the bent plate annotation refer to?",
            "What dimensions are associated with the bent plate?",
            "What is the bolt specification on this drawing?",
            "Which callout points at the purlin?",
            "What does the leader on sheet 2 label?",
            "What revision is this drawing at?",
        ],
    )
    async def test_a_drawing_question_is_never_answered_from_model_knowledge(self, query):
        """The observed failure: `llm_knowledge` at 0.8 confidence, and a
        textbook paragraph about bending sheet metal in place of the drawing's
        own answer."""
        router = QueryRouter(_FixedClassifier(Route.LLM_KNOWLEDGE))
        decision = await router.route(query)
        assert decision.route is Route.RAG
        assert "document and drawing vocabulary" in decision.reason

    async def test_a_document_question_is_not_sent_to_the_web(
        self, query="What does our latest drawing revision say?"
    ):
        router = QueryRouter(_FixedClassifier(Route.WEB_SEARCH))
        assert (await router.route(query)).route is Route.RAG

    async def test_the_override_keeps_the_classifier_confidence(self):
        router = QueryRouter(_FixedClassifier(Route.LLM_KNOWLEDGE, confidence=0.77))
        decision = await router.route("What does the bent plate annotation refer to?")
        assert decision.confidence == pytest.approx(0.77)
        assert decision.source is RouteSource.FALLBACK


@pytest.mark.asyncio
class TestGeneralQuestionsAreLeftAlone:
    @pytest.mark.parametrize(
        "query",
        [
            "Explain the difference between yield and ultimate strength",
            "Write me a haiku about rain",
            "Why is the sky blue?",
        ],
    )
    async def test_a_genuinely_general_question_keeps_its_route(self, query):
        """The override must not swallow the route it was added beside. These
        carry no document vocabulary, so nothing should change."""
        router = QueryRouter(_FixedClassifier(Route.LLM_KNOWLEDGE))
        assert (await router.route(query)).route is Route.LLM_KNOWLEDGE

    async def test_rag_decisions_pass_through_untouched(self):
        classifier = _FixedClassifier(Route.RAG)
        router = QueryRouter(classifier)
        decision = await router.route("What does the bent plate annotation refer to?")
        assert decision.route is Route.RAG
        assert decision.source is RouteSource.LLM

    async def test_a_greeting_never_reaches_the_classifier(self):
        classifier = _FixedClassifier(Route.LLM_KNOWLEDGE)
        router = QueryRouter(classifier)
        assert (await router.route("hello")).route is Route.GREETING
        assert classifier.calls == 0


@pytest.mark.asyncio
class TestScheduleQuestionsReachRetrieval:
    """A schedule tabulates marks and materials, and questions about them use
    ordinary words that the vocabulary list alone does not catch."""

    @pytest.mark.parametrize(
        "query",
        [
            "What material is BP-1?",
            "What is the quantity of BP-1?",
            "What is the weight of CE-4?",
            "Show me the schedule row for CE-4.",
            "Which members use ASTM A36?",
            "What grade is AB-2?",
        ],
    )
    async def test_a_schedule_question_is_never_answered_from_model_knowledge(self, query):
        """Asked "What material is BP-1?" the classifier answered that BP-1 is
        a biodegradable plastic. It is a 25mm mild steel base plate, and the
        schedule on the sheet says so."""
        router = QueryRouter(_FixedClassifier(Route.LLM_KNOWLEDGE))
        assert (await router.route(query)).route is Route.RAG

    async def test_a_bare_piece_mark_is_a_document_signal(self):
        """Nothing outside a drawing set has an opinion about what BP-1 is."""
        from src.routing.rules import mentions_documents

        assert mentions_documents("BP-1")
        assert mentions_documents("tell me about CE-4")
        assert not mentions_documents("tell me about photosynthesis")


@pytest.mark.asyncio
class TestVisualQuestionsReachRetrieval:
    """A symbol question is the one kind that may escalate to vision, and it
    can only do that from the retrieval path."""

    @pytest.mark.parametrize(
        "query",
        [
            "What does this unlabelled symbol represent?",
            "What symbol is shown inside this highlighted connection?",
            "What is this hatching?",
            "What does this callout mean?",
        ],
    )
    async def test_a_symbol_question_is_never_answered_from_model_knowledge(self, query):
        """Routed to general knowledge it produces a paragraph about symbols
        in general and never looks at the drawing at all."""
        router = QueryRouter(_FixedClassifier(Route.LLM_KNOWLEDGE))
        assert (await router.route(query)).route is Route.RAG
