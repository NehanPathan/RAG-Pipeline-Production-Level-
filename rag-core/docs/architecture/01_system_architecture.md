# System Architecture — Enterprise Agentic RAG Platform

## 1. Overview

An enterprise-grade Retrieval-Augmented Generation (RAG) platform designed to handle millions of documents and thousands of concurrent users. The system follows Clean Architecture with Domain-Driven Design, offering pluggable providers for every layer.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENTS                                       │
│   Streamlit UI       REST API Clients       CLI / SDKs              │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────────┐
│                    API GATEWAY (FastAPI)                              │
│   Auth Middleware │ Rate Limiter │ Request Validation │ CORS         │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
┌─────────▼──────────┐   ┌──────────▼──────────────────────────────┐
│  INGESTION SERVICE │   │          QUERY SERVICE                    │
│                    │   │                                           │
│  DocumentLoader    │   │  ┌─────────────┐   ┌──────────────────┐  │
│  ContentExtractor  │   │  │ Query Agent │   │ HybridRetriever  │  │
│  MetadataEnricher  │   │  │ (Small LLM) │   │ Vector + BM25    │  │
│  TableParser       │   │  │ Rewrite     │   │ RRF Fusion       │  │
│  SemanticChunker   │   │  │ Expand      │   │ Reranker         │  │
│  ParentChildChunk  │   │  │ IntentDetect│   └──────────────────┘  │
│  EmbeddingGen      │   │  │ FilterGen   │            │            │
└──────┬─────────────┘   │  └─────────────┘            │            │
       │                 │          │                   │            │
       │                 │          └──────┬────────────┘            │
       │                 │                 │                          │
       │                 │  ┌──────────────▼──────────────────────┐  │
       │                 │  │    Context Compression (Small LLM)  │  │
       │                 │  └──────────────┬──────────────────────┘  │
       │                 │                 │                          │
       │                 │  ┌──────────────▼──────────────────────┐  │
       │                 │  │    Answer Generation (Large LLM)    │  │
       │                 │  └──────────────┬──────────────────────┘  │
       │                 │                 │                          │
       │                 │  ┌──────────────▼──────────────────────┐  │
       │                 │  │    Citation Builder                  │  │
       │                 │  └──────────────┬──────────────────────┘  │
       │                 └─────────────────┼──────────────────────── ┘
       │                                   │
       │            ┌──────────────────────┘
       │            │
┌──────▼────────────▼────────────────────────────────────────────────┐
│                      INFRASTRUCTURE LAYER                            │
│                                                                      │
│   PostgreSQL      Qdrant         Elasticsearch    Redis             │
│   (metadata,      (vectors,       (BM25 search,   (caching,        │
│    documents,      embeddings)     full-text)       sessions)       │
│    evaluations,                                                      │
│    feedback)                                                         │
└────────────────────────────────────────────────────────────────────┘
       │            │
┌──────▼────────────▼────────────────────────────────────────────────┐
│                   OBSERVABILITY LAYER                                │
│   Langfuse (LLM tracing)  │  OpenTelemetry (traces)                │
│   Prometheus (metrics)     │  Structured Logging (JSON)             │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. Architectural Principles

| Principle | Decision | Rationale |
|-----------|----------|-----------|
| Clean Architecture | Domain → Application → Infrastructure | Domain logic stays framework-free and testable |
| DDD | Entities, Value Objects, Repositories, Services | Models the problem domain explicitly |
| Repository Pattern | Abstract interfaces for all data stores | Swap Qdrant → Weaviate without touching business logic |
| Service Layer | Use Cases orchestrate domain logic | Clear separation of concerns |
| Dependency Injection | `dependency-injector` library | Testability and configuration-driven wiring |
| Async-First | `asyncio` throughout, async DB/HTTP clients | Throughput for concurrent users |
| Provider Pattern | Pluggable LLMs, embeddings, vector DBs | No vendor lock-in |
| Event-Driven (future) | Kafka for async ingestion pipeline | Scale ingestion independently |

---

## 4. System Layers

### 4.1 Ingestion Pipeline

```
Raw Document
    │
    ▼
DocumentLoader          ← Docling / Unstructured / LlamaParse
    │
    ▼
ContentExtractor        ← Text, tables, image references
    │
    ▼
DocumentCleaner         ← Normalize whitespace, remove boilerplate
    │
    ▼
MetadataExtractor       ← Filename, filetype, size, page count, dates
    │
    ▼
LLMMetadataEnricher     ← Small LLM: tags, summary, domain, entities
    │
    ▼
TableAwareChunker       ← Preserve table structure, semantic chunking
    │
    ▼
ParentChildChunker      ← Large parent chunks + small child chunks
    │
    ▼
EmbeddingGenerator      ← OpenAI / BGE-M3 / E5-Large
    │
    ▼
VectorStoreWriter       ← Qdrant (primary)
    │
    ▼
SearchIndexWriter       ← Elasticsearch BM25 index
    │
    ▼
MetadataDBWriter        ← PostgreSQL document registry
```

### 4.2 Query Pipeline

```
User Query
    │
    ▼
QueryAgent (Small LLM)
    ├── QueryRewriter         ← Fix grammar, clarify ambiguity
    ├── QueryExpander         ← Generate 3-5 variant queries
    ├── IntentClassifier      ← policy_lookup / factual / analytical
    ├── DomainDetector        ← HR / Legal / Finance / Ops
    └── MetadataFilterGen     ← {source: "returns_docs", date_gte: ...}
    │
    ▼
HybridRetriever
    ├── VectorSearcher        ← Top-K from Qdrant
    ├── BM25Searcher          ← Top-K from Elasticsearch
    ├── MetadataFilter        ← Apply generated filters
    └── RRFFusion             ← Reciprocal Rank Fusion
    │
    ▼
Reranker (BGE / Cohere)       ← Cross-encoder reranking
    │
    ▼
ContextCompressor (Small LLM) ← Filter irrelevant chunks, compress
    │
    ▼
AnswerGenerator (Large LLM)   ← Generate answer with citations
    │
    ▼
CitationBuilder               ← Attach source/doc/chunk/page
    │
    ▼
StreamingResponse             ← SSE stream to client
```

### 4.3 Evaluation Pipeline

```
Evaluation Trigger (scheduled / on-demand)
    │
    ├── OfflineEvaluator
    │       ├── RAGAS evaluator
    │       │     ├── Faithfulness
    │       │     ├── Answer Relevancy
    │       │     ├── Context Precision
    │       │     └── Context Recall
    │       └── DeepEval evaluator
    │             └── Hallucination Score
    │
    └── OnlineEvaluator
            ├── User Feedback collector (thumbs up/down)
            ├── Latency tracker
            └── Retrieval quality metrics
```

---

## 5. LLM Provider Strategy

```
Provider Registry
    ├── OpenAI (GPT-4o-mini as small, GPT-4o as large)
    ├── Anthropic (claude-haiku-4-5 as small, claude-sonnet-4-6 as large)
    ├── Google (gemini-flash as small, gemini-pro as large)
    └── Ollama (local deployment, configurable)

Selection Strategy:
    - Small LLM: query rewriting, metadata enrichment, context compression
    - Large LLM: final answer generation
    - Configurable per-deployment via admin panel
```

---

## 6. Embedding Strategy

```
EmbeddingProvider (abstract)
    ├── OpenAIEmbeddingProvider   ← text-embedding-3-large (3072 dims)
    ├── BGEM3EmbeddingProvider    ← bge-m3 (1024 dims, multilingual)
    └── E5LargeEmbeddingProvider  ← e5-large-v2 (1024 dims)

Batch size: 100 documents per batch
Async embedding with retry/backoff
Embedding cache in Redis (TTL: 24h)
```

---

## 7. Security Architecture

```
Authentication:
    API Key validation  ← X-API-Key header
    JWT tokens          ← Bearer token, 1h expiry, refresh token 7d

Authorization:
    RBAC roles:
        admin    → full access
        editor   → upload, manage documents
        viewer   → read-only, chat only

Rate Limiting:
    Redis-backed sliding window
    Per-user: 100 req/min
    Per-API-key: 1000 req/min

Input Validation:
    Pydantic models on all inputs
    File type whitelist: PDF, DOCX, TXT, MD, HTML
    Max file size: 100MB
    Prompt injection detection (future)
```

---

## 8. Caching Strategy

```
Layer 1: Embedding Cache (Redis)
    Key: sha256(text + model_id)
    TTL: 24 hours
    Reduces embedding API costs by ~60% for repeated content

Layer 2: Query Cache (Redis)
    Key: sha256(query + filters + config_hash)
    TTL: 1 hour
    Invalidated on new document ingestion

Layer 3: Retrieval Cache (Redis)
    Key: sha256(rewritten_queries + filters)
    TTL: 30 minutes

Layer 4: LLM Response Cache (Redis)
    Key: sha256(context + query + model_id)
    TTL: 15 minutes
    Disabled for streaming responses
```

---

## 9. Scalability Design

```
Horizontal Scaling:
    - API service: stateless, scale with k8s HPA
    - Ingestion workers: Celery + Redis queue
    - Streamlit: single instance per deployment (stateful)

Vertical Scaling:
    - Qdrant: persistent storage, memory-mapped index
    - PostgreSQL: read replicas for evaluation queries
    - Elasticsearch: multi-node cluster

Async Processing:
    - Document ingestion is async (background task)
    - Webhook notification on completion
    - Progress tracking via Redis pub/sub
```

---

## 10. Disaster Recovery

| Component | Backup Strategy | RTO | RPO |
|-----------|----------------|-----|-----|
| PostgreSQL | Daily snapshots + WAL streaming | 1h | 5min |
| Qdrant | Snapshot API, S3 upload | 4h | 1h |
| Elasticsearch | Snapshot/Restore to S3 | 4h | 1h |
| Redis | RDB + AOF persistence | 15min | 1min |
| Documents | Original files in S3/local | N/A | N/A |
