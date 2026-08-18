from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievalTrace:
    """Per-stage latency/count breakdown for a single query.

    Populated incrementally across Modules A-E (each stage fills in its own
    fields) and surfaced via the /retrieval/inspect endpoint. Field names
    mirror the `latency_breakdown` shape in docs/architecture/07_api_design.md.

    `total_ms` is not derived by summing the per-stage fields, since
    vector_search_ms/bm25_search_ms run concurrently — summing would
    overstate wall-clock latency. It's set directly by whichever orchestrator
    measures the actual end-to-end wall-clock time.
    """

    queries_used: list[str] = field(default_factory=list)

    vector_count: int = 0
    bm25_count: int = 0
    fused_count: int = 0
    reranked_count: int = 0

    query_processing_ms: int = 0
    vector_search_ms: int = 0
    bm25_search_ms: int = 0
    fusion_ms: int = 0
    reranking_ms: int = 0
    total_ms: int = 0
