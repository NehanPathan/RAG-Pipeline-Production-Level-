# Sequence Diagrams — Enterprise Agentic RAG Platform

## 1. Document Ingestion Flow

```
User          Streamlit UI     FastAPI         IngestionSvc     External
  │                │               │                │            Services
  │  Upload PDF    │               │                │                │
  │───────────────►│               │                │                │
  │                │  POST /docs   │                │                │
  │                │──────────────►│                │                │
  │                │               │ Validate file  │                │
  │                │               │ Auth check     │                │
  │                │               │ Save to tmp    │                │
  │                │               │────────────────►                │
  │                │               │  IngestDocumentUC               │
  │                │               │                │ Load file      │
  │                │               │                │──────────────► │
  │                │               │                │ DoclingLoader  │
  │                │               │                │◄────────────── │
  │                │               │                │ RawContent     │
  │                │               │                │                │
  │                │               │                │ Extract text   │
  │                │               │                │ Extract tables │
  │                │               │                │ Extract meta   │
  │                │               │                │                │
  │                │               │                │ LLM Enrich     │
  │                │               │                │──────────────► │
  │                │               │                │ SmallLLM API   │
  │                │               │                │◄────────────── │
  │                │               │                │ Tags/Summary   │
  │                │               │                │                │
  │                │               │                │ SemanticChunk  │
  │                │               │                │ ParentChild    │
  │                │               │                │                │
  │                │               │                │ Embed chunks   │
  │                │               │                │──────────────► │
  │                │               │                │ EmbeddingAPI   │
  │                │               │                │◄────────────── │
  │                │               │                │ Vectors        │
  │                │               │                │                │
  │                │               │                │ Write Qdrant   │
  │                │               │                │ Write ES       │
  │                │               │                │ Write Postgres │
  │                │               │                │                │
  │                │  202 Accepted │                │                │
  │                │◄──────────────│                │                │
  │  Task ID shown │               │                │                │
  │◄───────────────│               │                │                │
  │                │               │                │                │
  │  Poll status   │               │                │                │
  │───────────────►│GET /docs/{id} │                │                │
  │                │──────────────►│                │                │
  │                │  Status: done │                │                │
  │◄───────────────│               │                │                │
```

---

## 2. Query / Chat Flow

```
User          Streamlit      FastAPI        QueryAgent      Retriever      LLMs
  │               │               │               │               │           │
  │  Type query   │               │               │               │           │
  │──────────────►│               │               │               │           │
  │               │ POST /chat    │               │               │           │
  │               │ (SSE stream)  │               │               │           │
  │               │──────────────►│               │               │           │
  │               │               │ Auth + Rate   │               │           │
  │               │               │ limit check   │               │           │
  │               │               │──────────────►│               │           │
  │               │               │               │ Rewrite query │           │
  │               │               │               │──────────────────────────►│
  │               │               │               │               │ Small LLM │
  │               │               │               │◄──────────────────────────│
  │               │               │               │ Rewritten Q   │           │
  │               │               │               │ Expanded Qs   │           │
  │               │               │               │ Intent        │           │
  │               │               │               │ Domain        │           │
  │               │               │               │ Filters       │           │
  │               │               │◄──────────────│               │           │
  │               │               │               │               │           │
  │               │               │──────────────────────────────►│           │
  │               │               │               │  HybridSearch │           │
  │               │               │               │  VectorSearch │           │
  │               │               │               │  BM25Search   │           │
  │               │               │               │  RRFFusion    │           │
  │               │               │               │  Reranking    │           │
  │               │               │◄──────────────────────────────│           │
  │               │               │               │ Top-10 chunks │           │
  │               │               │               │               │           │
  │               │               │──────────────────────────────────────────►│
  │               │               │               │ ContextCompress (SmallLLM)│
  │               │               │◄──────────────────────────────────────────│
  │               │               │               │ Compressed ctx│           │
  │               │               │               │               │           │
  │               │               │──────────────────────────────────────────►│
  │               │               │               │ AnswerGenerate (LargeLLM) │
  │               │               │               │               │  STREAM   │
  │  SSE tokens   │◄──────────────│               │               │ tokens──► │
  │◄──────────────│               │               │               │           │
  │  (streaming)  │               │               │               │           │
  │               │               │               │               │           │
  │  [END STREAM] │               │               │               │           │
  │               │               │               │               │           │
  │               │               │  CitationBuild│               │           │
  │               │               │  PostProcess  │               │           │
  │               │               │  Langfuse log │               │           │
  │               │               │  Metrics emit │               │           │
```

---

## 3. Authentication Flow

```
Client            FastAPI           PostgreSQL        Redis
  │                   │                 │               │
  │  POST /auth/token │                 │               │
  │  {api_key: "..."}│                 │               │
  │──────────────────►│                 │               │
  │                   │ Lookup API key  │               │
  │                   │────────────────►│               │
  │                   │◄────────────────│               │
  │                   │ User + roles    │               │
  │                   │                 │               │
  │                   │ Generate JWT    │               │
  │                   │ Cache session   │               │
  │                   │────────────────────────────────►│
  │                   │                 │               │
  │  200 {access_token, refresh_token} │               │
  │◄──────────────────│                 │               │
  │                   │                 │               │
  │  GET /documents   │                 │               │
  │  Bearer: JWT      │                 │               │
  │──────────────────►│                 │               │
  │                   │ Verify JWT      │               │
  │                   │ Check Redis     │               │
  │                   │────────────────────────────────►│
  │                   │◄────────────────────────────────│
  │                   │ Session valid   │               │
  │                   │ Check RBAC      │               │
  │  200 [documents]  │                 │               │
  │◄──────────────────│                 │               │
```

---

## 4. Evaluation Flow

```
Scheduler       EvalService       RAGASEval      DeepEval      PostgreSQL
   │                │                 │               │               │
   │ Trigger eval   │                 │               │               │
   │───────────────►│                 │               │               │
   │                │ Load eval set   │               │               │
   │                │────────────────────────────────────────────────►│
   │                │◄────────────────────────────────────────────────│
   │                │ {questions, ground_truth}       │               │
   │                │                 │               │               │
   │                │ Run RAG for     │               │               │
   │                │ each question   │               │               │
   │                │ (answer + ctx)  │               │               │
   │                │                 │               │               │
   │                │────────────────►│               │               │
   │                │                 │ RAGAS eval    │               │
   │                │                 │ faithfulness  │               │
   │                │                 │ relevancy     │               │
   │                │                 │ precision     │               │
   │                │                 │ recall        │               │
   │                │◄────────────────│               │               │
   │                │ RAGAS scores    │               │               │
   │                │                 │               │               │
   │                │────────────────────────────────►│               │
   │                │                 │ DeepEval      │               │
   │                │                 │ hallucination │               │
   │                │◄────────────────────────────────│               │
   │                │                 │               │               │
   │                │ Save results    │               │               │
   │                │────────────────────────────────────────────────►│
   │                │                 │               │               │
   │                │ Emit metrics    │               │               │
   │                │ (Prometheus)    │               │               │
```

---

## 5. Feedback Loop Flow

```
User         Streamlit        FastAPI         PostgreSQL      Prometheus
  │               │               │                │               │
  │  Thumbs Up    │               │                │               │
  │──────────────►│               │                │               │
  │               │ POST /feedback│                │               │
  │               │──────────────►│                │               │
  │               │               │ Validate       │               │
  │               │               │ Save feedback  │               │
  │               │               │───────────────►│               │
  │               │               │◄───────────────│               │
  │               │               │                │               │
  │               │               │ Increment      │               │
  │               │               │ feedback_total │               │
  │               │               │──────────────────────────────► │
  │               │               │                │               │
  │               │  201 Created  │                │               │
  │◄──────────────│               │                │               │
```

---

## 6. Retrieval Inspector Flow

```
Developer      Streamlit        FastAPI         QueryPipeline    Stores
    │               │               │                 │              │
    │ Enter query   │               │                 │              │
    │ (debug mode)  │               │                 │              │
    │──────────────►│               │                 │              │
    │               │ GET /retrieval│                 │              │
    │               │ /inspect      │                 │              │
    │               │──────────────►│                 │              │
    │               │               │ Run full query  │              │
    │               │               │ pipeline with   │              │
    │               │               │ debug=True      │              │
    │               │               │────────────────►│              │
    │               │               │                 │ Search all   │
    │               │               │                 │──────────────►
    │               │               │                 │◄─────────────
    │               │               │                 │              │
    │               │               │◄────────────────│              │
    │               │               │ {               │              │
    │               │               │   rewritten_queries,           │
    │               │               │   intent,                      │
    │               │               │   vector_results: [{           │
    │               │               │     chunk, vector_score}],     │
    │               │               │   bm25_results: [{             │
    │               │               │     chunk, bm25_score}],       │
    │               │               │   fused_results: [{            │
    │               │               │     chunk, rrf_score}],        │
    │               │               │   reranked_results: [{         │
    │               │               │     chunk, rerank_score}]      │
    │               │               │ }                              │
    │  Debug panel  │               │                 │              │
    │◄──────────────│               │                 │              │
```
