# Component Diagram — Enterprise Agentic RAG Platform

## 1. Top-Level Component Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRESENTATION LAYER                                  │
├───────────────────┬──────────────────────┬──────────────────────────────────┤
│  Chat Page        │  Document Mgmt Page  │  Admin Panel                     │
│  ─────────────    │  ──────────────────  │  ───────────                     │
│  • Streaming SSE  │  • Upload/Delete     │  • Model Selection               │
│  • Citations UI   │  • Reindex           │  • Embedding Selection           │
│  • Conv. History  │  • Metadata Viewer   │  • Retrieval Settings            │
├───────────────────┴──────────────────────┴──────────────────────────────────┤
│  Retrieval Inspector      │        Evaluation Dashboard                      │
│  ─────────────────────    │        ─────────────────────                     │
│  • Chunk Viewer           │        • RAGAS Scores                            │
│  • Score Comparison       │        • Trend Charts                            │
│  • Debug Mode             │        • Feedback Summary                        │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
                               HTTP / WebSocket
                                     │
┌────────────────────────────────────▼───────────────────────────────────────┐
│                              API LAYER (FastAPI)                             │
├─────────────┬───────────────┬──────────────┬──────────────┬────────────────┤
│  Auth       │  Documents    │  Chat        │  Eval        │  Admin         │
│  Router     │  Router       │  Router      │  Router      │  Router        │
│  ─────────  │  ──────────   │  ─────────   │  ─────────   │  ───────────   │
│  /auth/*    │  /documents/* │  /chat/*     │  /eval/*     │  /admin/*      │
│  /tokens/*  │  /chunks/*    │  /stream/*   │  /metrics/*  │  /settings/*   │
├─────────────┴───────────────┴──────────────┴──────────────┴────────────────┤
│  Middleware Stack                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│  RequestID  │  AuthMiddleware  │  RateLimiter  │  CORS  │  TracingMiddleware │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼───────────────────────────────────────┐
│                          APPLICATION LAYER (Use Cases)                       │
├──────────────────────────┬─────────────────────────────────────────────────┤
│  INGESTION USE CASES     │  QUERY USE CASES                                 │
│  ────────────────────    │  ──────────────────                               │
│  • IngestDocumentUC      │  • ProcessQueryUC                                │
│  • ReindexDocumentUC     │  • StreamAnswerUC                                │
│  • DeleteDocumentUC      │  • InspectRetrievalUC                            │
│  • UpdateMetadataUC      │  • GetConversationUC                             │
├──────────────────────────┴─────────────────────────────────────────────────┤
│  EVALUATION USE CASES    │  ADMIN USE CASES                                 │
│  ────────────────────    │  ────────────────                                 │
│  • RunRAGASEvalUC        │  • UpdateConfigUC                                │
│  • RecordFeedbackUC      │  • GetSystemStatusUC                             │
│  • GetMetricsUC          │  • ManageAPIKeysUC                               │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼───────────────────────────────────────┐
│                           DOMAIN LAYER                                       │
├──────────────────┬──────────────────┬──────────────┬────────────────────────┤
│  ENTITIES        │  VALUE OBJECTS   │  DOMAIN      │  DOMAIN EVENTS         │
│  ─────────────   │  ─────────────── │  SERVICES    │  ──────────────────    │
│  • Document      │  • ChunkContent  │  ─────────── │  • DocumentIngested    │
│  • DocumentChunk │  • EmbeddingVec  │  • Chunking  │  • QueryProcessed      │
│  • Conversation  │  • Citation      │  • Scoring   │  • EvaluationCompleted │
│  • Message       │  • Metadata      │  • Ranking   │  • FeedbackReceived    │
│  • Evaluation    │  • QueryIntent   │              │                        │
│  • Feedback      │  • SearchFilter  │              │                        │
└──────────────────┴──────────────────┴──────────────┴────────────────────────┘
                                     │
┌────────────────────────────────────▼───────────────────────────────────────┐
│                        INFRASTRUCTURE LAYER                                  │
├───────────────┬──────────────┬──────────────────┬──────────────────────────┤
│  INGESTION    │  RETRIEVAL   │  LLM             │  PERSISTENCE             │
│  ──────────   │  ─────────   │  ─────────────   │  ──────────────────────  │
│  Docling      │  VectorRepo  │  LLMProvider     │  PostgreSQL (SQLAlchemy) │
│  Unstructured │  BM25Repo    │  (OpenAI/        │  Qdrant Client           │
│  LlamaParse   │  RerankerSvc │  Anthropic/      │  Elasticsearch Client    │
│  Chunkers     │  RRFFusion   │  Gemini/Ollama)  │  Redis Client            │
│  EmbeddingPvd │              │  EmbeddingPvd    │  S3 Client (optional)    │
├───────────────┴──────────────┴──────────────────┴──────────────────────────┤
│  OBSERVABILITY                                                               │
│  ─────────────────────────────────────────────────────────────────────────  │
│  Langfuse Client  │  OTel Tracer  │  Prometheus Metrics  │  JSON Logger     │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Ingestion Component Detail

```
IngestionPipeline
│
├── DocumentLoaderFactory
│       ├── DoclingLoader          ← PDF, DOCX (best table extraction)
│       ├── UnstructuredLoader     ← HTML, TXT, MD, fallback
│       └── LlamaParseLoader       ← Optional paid API, high quality PDF
│
├── ContentExtractor
│       ├── TextExtractor          ← Main body text
│       ├── TableExtractor         ← Tables → markdown or JSON
│       └── ImageReferenceExtractor ← Extract image captions/references
│
├── DocumentCleaner
│       ├── WhitespaceNormalizer
│       ├── HeaderFooterRemover
│       └── BoilerplateDetector
│
├── MetadataExtractor
│       └── FileMetadata (name, type, size, page_count, created_at)
│
├── LLMMetadataEnricher
│       ├── SummaryGenerator       ← 2-3 sentence summary
│       ├── TagExtractor           ← Domain tags, entity tags
│       ├── DomainClassifier       ← HR / Legal / Finance / Ops / etc.
│       └── EntityExtractor        ← Named entities (people, orgs, dates)
│
├── ChunkingStrategy
│       ├── SemanticChunker        ← Embedding-based sentence boundaries
│       ├── TokenChunker           ← Fixed token size with overlap
│       └── ParentChildChunker     ← Large parent + small child chunks
│                                      Parent: 1024 tokens
│                                      Child: 256 tokens, 32 overlap
│
├── EmbeddingGenerator
│       ├── Batch processor (100 chunks/batch)
│       ├── Retry with exponential backoff
│       └── Redis embedding cache
│
└── StorageWriters
        ├── QdrantWriter           ← Vectors + payload
        ├── ElasticsearchWriter    ← BM25 index
        └── PostgresWriter         ← Document registry + chunk metadata
```

---

## 3. Query Component Detail

```
QueryPipeline
│
├── QueryAgent
│       ├── QueryPreprocessor      ← Lowercase, strip PII (optional)
│       ├── QueryRewriter          ← Fix typos, clarify ambiguity
│       │     Prompt: "Rewrite for clarity: {query}"
│       ├── QueryExpander          ← 3-5 variant queries
│       │     Prompt: "Generate search variants for: {query}"
│       ├── IntentClassifier       ← policy_lookup|factual|analytical|comparison
│       ├── DomainDetector         ← HR|Legal|Finance|Operations|General
│       └── MetadataFilterGenerator ← JSON filter spec
│             Prompt: "Extract search filters from: {query}"
│             Output: {"source_tags": [...], "date_range": {...}, "doc_type": "..."}
│
├── HybridRetriever
│       ├── VectorSearcher
│       │     ├── Query: original + expanded variants
│       │     ├── Top-K: 20 per variant
│       │     └── Filter: generated metadata filters
│       │
│       ├── BM25Searcher
│       │     ├── Query: original + expanded variants
│       │     ├── Top-K: 20 per variant
│       │     └── Filter: metadata field filters
│       │
│       └── RRFFusion
│             ├── Merge vector results (all variants)
│             ├── Merge BM25 results (all variants)
│             └── RRF formula: score = Σ 1/(k + rank_i), k=60
│
├── Reranker
│       ├── BGEReranker            ← BAAI/bge-reranker-large
│       └── CohereReranker         ← cohere.rerank API
│             Input: top-40 fused chunks
│             Output: top-10 reranked chunks
│
├── ContextCompressor (Small LLM)
│       ├── ChunkRelevanceFilter   ← Remove clearly irrelevant chunks
│       ├── ContentSummarizer      ← Compress verbose chunks
│       └── DeduplicationFilter    ← Remove near-duplicate content
│
├── AnswerGenerator (Large LLM)
│       ├── SystemPromptBuilder    ← Role + instructions + citation format
│       ├── ContextAssembler       ← Format chunks with markers
│       ├── StreamingGenerator     ← SSE token stream
│       └── ResponseValidator      ← Check for refusals, truncation
│
└── CitationBuilder
        ├── SourceMapper           ← chunk_id → source metadata
        └── CitationFormatter      ← [1] Source: doc.pdf, p.5, chunk_id: abc123
```

---

## 4. Evaluation Component Detail

```
EvaluationSystem
│
├── OfflineEvaluator
│       ├── RAGASEvaluator
│       │     ├── FaithfulnessMetric
│       │     ├── AnswerRelevancyMetric
│       │     ├── ContextPrecisionMetric
│       │     └── ContextRecallMetric
│       │
│       └── DeepEvalEvaluator
│             └── HallucinationMetric
│
├── OnlineEvaluator
│       ├── FeedbackCollector      ← thumbs_up / thumbs_down + optional comment
│       ├── LatencyTracker         ← TTFB, total latency, streaming duration
│       └── RetrievalQualityTracker ← MRR, NDCG@10 (if ground truth available)
│
└── MetricsAggregator
        ├── TimeSeriesAggregator   ← Daily/weekly/monthly trends
        └── ReportGenerator        ← PDF/JSON reports (future)
```

---

## 5. Observability Component Detail

```
ObservabilitySystem
│
├── LangfuseTracer
│       ├── Trace: full query lifecycle
│       ├── Span: each pipeline step
│       └── Metadata: tokens, cost, latency per step
│
├── OpenTelemetryTracer
│       ├── HTTP request spans
│       ├── DB query spans
│       └── External API spans
│
├── PrometheusExporter
│       ├── rag_query_latency_seconds (histogram)
│       ├── rag_documents_ingested_total (counter)
│       ├── rag_retrieval_chunks_count (gauge)
│       ├── rag_llm_tokens_total (counter by model/type)
│       ├── rag_embedding_cache_hits_total (counter)
│       └── rag_evaluation_score (gauge by metric)
│
└── StructuredLogger
        ├── JSON format (structlog)
        ├── Log levels: DEBUG/INFO/WARNING/ERROR/CRITICAL
        └── Fields: request_id, user_id, trace_id, duration_ms
```

---

## 6. Component Dependencies Matrix

| Component | Depends On | Exposes |
|-----------|-----------|---------|
| API Layer | Application Use Cases, Auth | HTTP endpoints |
| Use Cases | Domain Services, Repositories | Async methods |
| Domain Services | Domain Entities | Pure functions |
| Repositories | Infrastructure Clients | Async CRUD |
| Ingestion Pipeline | Loaders, Chunkers, EmbeddingPvd, Repositories | Async pipeline |
| Query Pipeline | LLMProvider, Repositories, Reranker | Async stream |
| LLMProvider | External APIs (OpenAI/Anthropic/etc.) | Async completions |
| EmbeddingProvider | External APIs / Local models | Async vectors |
| Observability | All components (injected) | Metrics/traces |
