"""Assembles the query graph.

This replaces control flow that was previously spread across five methods of
`QueryPipeline`, each an async generator delegating to the next, with early
returns at eight different points. The branching was correct but only
legible by reading all five in order; here the same decisions are edges you
can see at once -- and the fallback from a failed tool back into full
retrieval, which was a recursive call into another generator, is just an
edge.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from src.retrieval.graph.nodes import QueryNodes
from src.retrieval.graph.state import QueryState
from src.routing.routes import Route


def _stop_if_finished(state: QueryState) -> Literal["continue", "stop"]:
    return "stop" if state.get("finished") else "continue"


def _after_routing(state: QueryState) -> Literal["retrieve", "off_pipeline"]:
    decision = state.get("decision")
    if decision is None or decision.route is Route.RAG:
        return "retrieve"
    return "off_pipeline"


def _after_off_pipeline(state: QueryState) -> Literal["retrieve", "stop"]:
    # A tool the router chose was unavailable or failed. The user should get
    # an answer, not the router's misjudgement.
    return "retrieve" if state.get("fallback_to_rag") else "stop"


def build_query_graph(pipeline: Any) -> Any:
    """Compile the query graph for one pipeline instance."""
    nodes = QueryNodes(pipeline)
    graph: StateGraph[QueryState, None, QueryState, QueryState] = StateGraph(QueryState)

    graph.add_node("kill_switch", nodes.check_kill_switch)
    graph.add_node("cache", nodes.lookup_cache)
    graph.add_node("route", nodes.route_query)
    graph.add_node("off_pipeline", nodes.answer_off_pipeline)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("context", nodes.build_context)
    graph.add_node("generate", nodes.generate)

    graph.add_edge(START, "kill_switch")
    graph.add_conditional_edges(
        "kill_switch", _stop_if_finished, {"continue": "cache", "stop": END}
    )
    graph.add_conditional_edges("cache", _stop_if_finished, {"continue": "route", "stop": END})
    graph.add_conditional_edges(
        "route", _after_routing, {"retrieve": "retrieve", "off_pipeline": "off_pipeline"}
    )
    graph.add_conditional_edges(
        "off_pipeline", _after_off_pipeline, {"retrieve": "retrieve", "stop": END}
    )
    graph.add_edge("retrieve", "context")
    graph.add_conditional_edges(
        "context", _stop_if_finished, {"continue": "generate", "stop": END}
    )
    graph.add_edge("generate", END)

    return graph.compile()
