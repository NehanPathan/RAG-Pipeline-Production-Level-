"""State carried between nodes of the query graph.

Deliberately flat and explicit. Every key is optional (`total=False`)
because nodes only ever return the subset they actually computed, and
LangGraph merges those partial updates -- so a node that refuses cannot
accidentally blank out the trace id, and a node that never ran leaves no
half-populated fields behind.
"""

from __future__ import annotations

import uuid
from typing import Any

from typing_extensions import TypedDict

from src.domain.value_objects.context_bundle import CompressedChunk
from src.governance.rbac import Principal
from src.routing.routes import RouteDecision


class QueryState(TypedDict, total=False):
    # Request
    query: str
    user_id: uuid.UUID | None
    principal: Principal
    trace_id: str
    started_at: float
    flags: Any

    # Routing
    decision: RouteDecision
    # Set when the router chose a tool that turned out to be unavailable or
    # failed. The router's guess being wrong should cost latency, not an
    # answer, so the graph routes back into full retrieval.
    fallback_to_rag: bool

    # Retrieval (modules A-D) and context (module E)
    inspection: Any
    compressed_chunks: list[CompressedChunk]
    citations: dict[uuid.UUID, Any]

    # Generation (module G)
    answer: str
    citation_payload: list[dict[str, Any]]

    # Terminal control. `finished` is what every conditional edge reads to
    # decide whether to stop; nodes set it rather than raising, so a refusal
    # and a successful answer leave the graph the same way.
    finished: bool
