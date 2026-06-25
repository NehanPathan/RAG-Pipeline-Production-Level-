from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

registry = CollectorRegistry(auto_describe=True)

query_latency = Histogram(
    "rag_query_latency_seconds",
    "End-to-end query latency",
    ["intent", "provider"],
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

evaluation_score = Gauge(
    "rag_evaluation_score",
    "Latest evaluation metric score",
    ["metric", "dataset"],
    registry=registry,
)

active_users = Gauge(
    "rag_active_users",
    "Currently active users",
    registry=registry,
)
