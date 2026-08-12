"""Prometheus metric definitions.

Every metric declared here MUST be recorded somewhere — a declared-but-never
incremented metric reads as "0, everything is fine" on a dashboard, which is
worse than having no metric at all. `scripts/check_governance.py` enforces
this by failing CI when a metric has no recording call site.

Metrics referenced by `docs/governance/risk_register.yaml` are additionally
checked to exist here, so a risk can never point at a metric nobody emits.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry(auto_describe=True)

# ---------------------------------------------------------------- pipeline

query_latency = Histogram(
    "rag_query_latency_seconds",
    "End-to-end query latency",
    ["intent", "provider"],
    registry=registry,
)

stage_latency = Histogram(
    "rag_stage_latency_seconds",
    "Per-pipeline-stage latency, mirroring the spans TracedStage emits",
    ["stage"],
    # Retrieval stages finish in milliseconds while answer generation runs for
    # seconds; the default buckets bunch both into the same two cells.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

documents_ingested = Counter(
    "rag_documents_ingested_total",
    "Total documents ingested",
    ["file_type", "status"],
    registry=registry,
)

chunks_retrieved = Histogram(
    "rag_retrieval_chunks_count",
    "Number of chunks retrieved per query",
    ["stage"],
    buckets=(0, 1, 3, 5, 10, 20, 40, 80),
    registry=registry,
)

retrieval_filter_fallback = Counter(
    "rag_retrieval_filter_fallback_total",
    "Retrievals retried after LLM-inferred metadata filters matched nothing",
    ["recovered"],  # whether the relaxed retry actually found results
    registry=registry,
)

retrieval_empty = Counter(
    "rag_retrieval_empty_total",
    "Queries that retrieved nothing after reranking",
    registry=registry,
)

llm_tokens = Counter(
    "rag_llm_tokens_total",
    "Total LLM tokens used",
    ["model", "token_type"],
    registry=registry,
)

embedding_cache_hits = Counter(
    "rag_embedding_cache_hits_total",
    "Embedding cache hit count",
    ["result"],
    registry=registry,
)

semantic_cache_lookups = Counter(
    "rag_semantic_cache_lookups_total",
    "Semantic query cache lookups",
    ["result"],
    registry=registry,
)

answers_total = Counter(
    "rag_answers_total",
    "Answers produced, by outcome",
    ["outcome"],  # served | cached | refused | error
    registry=registry,
)

# NOTE: `rag_active_users` was removed rather than kept as a placeholder.
# It was declared but never recorded, so any dashboard panel using it showed
# a confident, permanent zero -- worse than an absent metric, because a zero
# reads as a measurement. Recording it truthfully needs session tracking this
# system does not have. `scripts/check_governance.py` now fails CI on any
# metric in this file with no recording call site, so the same gap cannot
# reappear silently.

# ---------------------------------------------------------------- measure

evaluation_score = Gauge(
    "rag_evaluation_score",
    "Latest offline evaluation metric score",
    ["metric", "dataset"],
    registry=registry,
)

online_eval_score = Histogram(
    "rag_online_eval_score",
    "Judge scores for sampled live traffic",
    ["metric"],
    buckets=(0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=registry,
)

online_eval_sampled = Counter(
    "rag_online_eval_sampled_total",
    "Live answers pulled for online scoring",
    ["result"],  # scored | skipped | failed
    registry=registry,
)

user_feedback = Counter(
    "rag_user_feedback_total",
    "Explicit user feedback on answers",
    ["sentiment", "tag"],
    registry=registry,
)

citation_validation = Counter(
    "rag_citation_validation_total",
    "Citation marker validation outcomes -- the primary hallucination signal",
    ["result"],  # valid | hallucinated_index | uncited_answer
    registry=registry,
)

# ---------------------------------------------------------------- govern

policy_violations = Counter(
    "rag_policy_violations_total",
    "Policy checks that failed, by the control that caught them",
    ["control_id"],
    registry=registry,
)

audit_events = Counter(
    "rag_audit_events_total",
    "Entries appended to the governance audit trail",
    ["action", "outcome"],
    registry=registry,
)

rate_limit_decisions = Counter(
    "rag_rate_limit_decisions_total",
    "Rate-limit checks by bucket and outcome (outcome=error means the limiter "
    "failed open and is not currently protecting anything)",
    ["bucket", "outcome"],
    registry=registry,
)

access_denied = Counter(
    "rag_access_denied_total",
    "Requests refused by RBAC or data classification",
    ["reason"],
    registry=registry,
)

pii_redactions = Counter(
    "rag_pii_redactions_total",
    "Personal data occurrences masked, by detector and where they were found",
    ["detector", "surface"],  # surface: context | answer | tool_result
    registry=registry,
)

retrieval_blocked_chunks = Counter(
    "rag_retrieval_blocked_chunks_total",
    "Retrieved chunks withheld because the principal lacked clearance",
    ["sensitivity"],
    registry=registry,
)

policy_info = Gauge(
    "rag_policy_info",
    "Always 1; labels carry the policy version and enforcement mode in force",
    ["version", "enforcement_mode"],
    registry=registry,
)

# ---------------------------------------------------------------- routing

router_decisions = Counter(
    "rag_router_decisions_total",
    "Query routing decisions, by chosen route and how it was decided",
    ["route", "source"],  # source: rule | llm | fallback | forced
    registry=registry,
)

retrieval_skipped = Counter(
    "rag_retrieval_skipped_total",
    "Queries answered without touching vector or keyword search",
    ["reason"],
    registry=registry,
)

tool_invocations = Counter(
    "rag_tool_invocations_total",
    "Tool executions dispatched by the router",
    ["tool", "outcome"],  # outcome: ok | error | denied | disabled
    registry=registry,
)

tool_latency = Histogram(
    "rag_tool_latency_seconds",
    "Tool execution latency",
    ["tool"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0),
    registry=registry,
)

# ------------------------------------------------------------ ai gateway

gateway_requests = Counter(
    "rag_llm_gateway_requests_total",
    "LLM calls through the gateway",
    ["provider", "role", "outcome"],
    registry=registry,
)

gateway_fallbacks = Counter(
    "rag_llm_gateway_fallbacks_total",
    "Times the gateway failed over from one provider to another",
    ["from_provider", "to_provider"],
    registry=registry,
)

gateway_cost_usd = Counter(
    "rag_llm_cost_usd_total",
    "Estimated LLM spend in USD, priced at the gateway",
    ["provider", "model"],
    registry=registry,
)

# ------------------------------------------------------------------ auth

auth_verifications = Counter(
    "rag_auth_verifications_total",
    "Identity token verification attempts",
    ["provider", "result"],  # result: ok | expired | invalid | missing | unavailable
    registry=registry,
)

# ---------------------------------------------------------------- manage

feature_flag_state = Gauge(
    "rag_feature_flag_state",
    "Feature flag state (1 = enabled). Covers capabilities and kill switches.",
    ["flag"],
    registry=registry,
)

# Kept alongside `feature_flag_state` rather than folded into it: this is the
# series alerts.yml and risk R-O01 reference by name, and renaming a series an
# alert depends on silently disables that alert.
kill_switch_state = Gauge(
    "rag_kill_switch_state",
    "Runtime flag state (1 = subsystem enabled, 0 = disabled by an operator)",
    ["flag"],
    registry=registry,
)

retention_purged = Counter(
    "rag_retention_purged_total",
    "Documents removed by the retention job",
    ["outcome"],
    registry=registry,
)


def metric_names() -> set[str]:
    """Every metric name currently registered, in both the family form
    (`rag_x`) and the exported counter form (`rag_x_total`).

    prometheus_client strips the `_total` suffix from a counter's family
    name, so a register entry naming `rag_policy_violations_total` would
    otherwise look absent. Both spellings are accepted.
    """
    names: set[str] = set()
    for metric in registry.collect():
        names.add(metric.name)
        if metric.type == "counter":
            names.add(f"{metric.name}_total")
    return names
