"""Tests for the query graph's control flow.

`test_query_pipeline.py` covers behaviour end to end through
`QueryPipeline.answer()` and passes unchanged against this implementation.
These tests target the routing decisions themselves, which were previously
early returns buried inside five async generators and had no direct coverage
at all.
"""

from src.retrieval.graph.builder import _after_off_pipeline, _after_routing, _stop_if_finished
from src.routing.routes import Route, RouteDecision, RouteSource


def _decision(route: Route) -> RouteDecision:
    return RouteDecision(route=route, source=RouteSource.RULE, reason="test")


class TestStopIfFinished:
    def test_continues_when_not_finished(self):
        assert _stop_if_finished({}) == "continue"

    def test_stops_once_a_node_has_emitted_a_terminal_event(self):
        assert _stop_if_finished({"finished": True}) == "stop"


class TestRouting:
    def test_rag_goes_to_retrieval(self):
        assert _after_routing({"decision": _decision(Route.RAG)}) == "retrieve"

    def test_a_missing_decision_defaults_to_retrieval(self):
        """No router configured means every query takes the RAG path, which is
        what the pipeline did before routing existed."""
        assert _after_routing({}) == "retrieve"

    def test_non_rag_routes_skip_retrieval_entirely(self):
        """Routes that need no documents must never touch embedding, vector
        search, BM25, fusion, reranking or compression -- that is the whole
        point of routing before answering."""
        for route in (Route.GREETING, Route.CALCULATOR, Route.REFUSE, Route.LLM_KNOWLEDGE):
            assert _after_routing({"decision": _decision(route)}) == "off_pipeline", route


class TestToolFallback:
    def test_a_failed_tool_falls_back_into_full_retrieval(self):
        """The router's guess was that a tool could answer this. Being wrong
        should cost latency, not an answer."""
        assert _after_off_pipeline({"fallback_to_rag": True}) == "retrieve"

    def test_a_successful_tool_answer_ends_the_graph(self):
        assert _after_off_pipeline({"finished": True}) == "stop"


class TestGraphShape:
    def test_graph_compiles_and_exposes_every_stage(self):
        """A node unreachable from START, or a conditional edge naming a node
        that does not exist, fails at compile time rather than on a request."""
        from unittest.mock import MagicMock

        from src.retrieval.graph import build_query_graph

        graph = build_query_graph(MagicMock())

        nodes = set(graph.get_graph().nodes)
        assert {
            "kill_switch",
            "cache",
            "route",
            "off_pipeline",
            "retrieve",
            "context",
            "generate",
        } <= nodes
