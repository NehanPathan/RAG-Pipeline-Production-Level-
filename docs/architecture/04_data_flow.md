# Data Flow Diagrams — Enterprise Agentic RAG Platform

## 1. Document Data Flow (Ingestion)

```
[Raw File]
    │
    │  Bytes (PDF/DOCX/TXT/MD/HTML)
    ▼
[DocumentLoader]
    │
    │  RawDocument {
    │    file_path: str
    │    file_name: str
    │    mime_type: str
    │    raw_content: bytes
    │    page_count: int | None
    │  }
    ▼
[ContentExtractor]
    │
    │  ExtractedContent {
    │    text_blocks: List[TextBlock]
    │    tables: List[TableBlock]
    │    image_refs: List[ImageRef]
    │    metadata: FileMetadata
    │  }
    ▼
[DocumentCleaner]
    │
    │  CleanedContent {
    │    cleaned_text: str
    │    normalized_tables: List[Table]
    │    metadata: FileMetadata
    │  }
    ▼
[LLMMetadataEnricher]
    │
    │  EnrichedDocument {
    │    content: CleanedContent
    │    enriched_metadata: {
    │      summary: str
    │      tags: List[str]
    │      domain: str
    │      entities: List[Entity]
    │      language: str
    │    }
    │  }
    ▼
[SemanticChunker + ParentChildChunker]
    │
    │  List[DocumentChunk] {
    │    chunk_id: UUID
    │    document_id: UUID
    │    parent_chunk_id: UUID | None
    │    content: str
    │    chunk_type: "parent" | "child"
    │    position: int
    │    token_count: int
    │    metadata: ChunkMetadata {
    │      page_number: int | None
    │      section: str | None
    │      contains_table: bool
    │    }
    │  }
    ▼
[EmbeddingGenerator]
    │
    │  List[ChunkWithEmbedding] {
    │    chunk: DocumentChunk
    │    embedding: List[float]   ← 1024 or 3072 dims
    │    model_id: str
    │  }
    │
    ├──────────────────────────────────────────────┐
    │                                              │
    ▼                                              ▼
[QdrantWriter]                            [PostgresWriter]
    │                                              │
    │  Qdrant Point {                              │  documents table
    │    id: UUID                                  │  document_chunks table
    │    vector: List[float]                       │  chunk_metadata table
    │    payload: {                                │
    │      chunk_id, document_id,                  │
    │      content, metadata,                      ▼
    │      domain, tags           [ElasticsearchWriter]
    │    }                                         │
    │  }                                           │  ES Document {
    │                                              │    chunk_id: UUID
    │                                              │    content: str
    │                                              │    metadata: {...}
    │                                              │  }
```

---

## 2. Query Data Flow

```
[User Input: "What is the refund policy?"]
    │
    │  UserMessage {
    │    query: str
    │    conversation_id: UUID
    │    user_id: UUID
    │    timestamp: datetime
    │  }
    ▼
[QueryAgent — Small LLM]
    │
    │  ProcessedQuery {
    │    original_query: str
    │    rewritten_query: str           ← "What is the product return and refund policy?"
    │    expanded_queries: List[str]    ← ["refund process", "return rules", "money back policy"]
    │    intent: QueryIntent {
    │      type: "policy_lookup"
    │      confidence: 0.95
    │    }
    │    domain: "operations"
    │    metadata_filters: SearchFilter {
    │      source_tags: ["returns_docs", "operations_docs"]
    │      doc_type: "policy"
    │      date_range: None
    │    }
    │  }
    ▼
[HybridRetriever]
    │
    ├── [VectorSearcher] ────────────────► Qdrant
    │       Input: rewritten + expanded Qs │
    │       Output: List[ScoredChunk]      │
    │               {chunk, score: 0.89}   │
    │                                      │
    ├── [BM25Searcher] ─────────────────► Elasticsearch
    │       Input: rewritten + expanded Qs │
    │       Output: List[ScoredChunk]      │
    │               {chunk, bm25_score}    │
    │                                      │
    └── [RRFFusion]                        │
            Input: both result lists       │
            Output: List[FusedChunk] {     │
              chunk: DocumentChunk         │
              vector_score: float          │
              bm25_score: float            │
              rrf_score: float             │
              rank: int                    │
            }
    │
    ▼
[Reranker]
    │
    │  Input: top-40 FusedChunks
    │  Output: List[RerankedChunk] {
    │    chunk: DocumentChunk
    │    vector_score: float
    │    bm25_score: float
    │    rrf_score: float
    │    rerank_score: float     ← cross-encoder score 0.0-1.0
    │    final_rank: int
    │  }
    │  (top-10 only)
    ▼
[ContextCompressor — Small LLM]
    │
    │  Input: top-10 RerankedChunks
    │  Output: CompressedContext {
    │    chunks: List[CompressedChunk] {
    │      original_chunk_id: UUID
    │      compressed_content: str    ← shorter, relevant excerpt
    │      relevance_score: float
    │    }
    │    total_tokens: int
    │  }
    ▼
[AnswerGenerator — Large LLM]
    │
    │  Input: CompressedContext + ProcessedQuery
    │  Output: AnswerStream {
    │    tokens: AsyncIterator[str]   ← SSE stream
    │    finish_reason: str
    │    usage: TokenUsage {
    │      prompt_tokens: int
    │      completion_tokens: int
    │      total_tokens: int
    │    }
    │  }
    ▼
[CitationBuilder]
    │
    │  FinalResponse {
    │    answer: str
    │    citations: List[Citation] {
    │      index: int               ← [1], [2] inline
    │      source_name: str         ← "HR Policy Manual"
    │      document_name: str       ← "hr_policy_2024.pdf"
    │      chunk_id: UUID
    │      page_number: int | None  ← 5
    │      section: str | None      ← "Section 3.2"
    │    }
    │    retrieval_debug: RetrievalDebug | None
    │    latency_ms: int
    │    model_used: str
    │    tokens_used: TokenUsage
    │  }
    ▼
[SSE Stream to Client]
```

---

## 3. Evaluation Data Flow

```
[Evaluation Dataset]
    │
    │  EvalDataset {
    │    items: List[EvalItem] {
    │      question: str
    │      ground_truth: str
    │      expected_sources: List[str]
    │    }
    │  }
    ▼
[Run RAG Pipeline]  ← for each item
    │
    │  RAGResult {
    │    question: str
    │    answer: str
    │    contexts: List[str]       ← retrieved chunk contents
    │    ground_truth: str
    │  }
    ▼
[RAGAS Evaluator]
    │
    │  RAGASResult {
    │    faithfulness: float         ← 0.0-1.0
    │    answer_relevancy: float
    │    context_precision: float
    │    context_recall: float
    │  }
    │
    ▼
[DeepEval Evaluator]
    │
    │  DeepEvalResult {
    │    hallucination_score: float  ← 0.0-1.0 (lower is better)
    │  }
    ▼
[EvaluationResult — saved to PostgreSQL]
    │
    │  EvaluationRun {
    │    run_id: UUID
    │    run_at: datetime
    │    dataset_name: str
    │    model_config: ModelConfig
    │    metrics: AggregatedMetrics {
    │      avg_faithfulness: float
    │      avg_answer_relevancy: float
    │      avg_context_precision: float
    │      avg_context_recall: float
    │      avg_hallucination_score: float
    │    }
    │    per_item_results: List[ItemResult]
    │  }
    ▼
[Prometheus Metrics Emit]
    │
    │  rag_evaluation_score{metric="faithfulness"} 0.87
    │  rag_evaluation_score{metric="relevancy"}    0.91
    │  ...
```

---

## 4. Observability Data Flow

```
[Any Pipeline Step]
    │
    │  Emits:
    ├─────────────────────────────────────────────────────────┐
    │                         │                               │
    ▼                         ▼                               ▼
[Langfuse]              [OpenTelemetry]                [Prometheus]
    │                         │                               │
    │  Trace {                │  Span {                       │
    │    trace_id: str        │    trace_id: str              │
    │    name: "rag_query"    │    name: "vector_search"      │
    │    input: query         │    duration_ms: 45            │
    │    output: answer       │    attributes: {              │
    │    metadata: {          │      db.system: "qdrant"      │
    │      model: str         │      k: 20                    │
    │      tokens: int        │    }                          │
    │      cost_usd: float    │  }                            │
    │      latency_ms: int    │                               │
    │    }                    │                               │
    │    spans: [             │                               │
    │      query_rewrite,     │                               │
    │      retrieval,         │  Metric {                     │
    │      rerank,            │    name: rag_query_latency    │
    │      compress,          │    value: 1250 ms             │
    │      generate           │    labels: {model, intent}    │
    │    ]                    │  }                            │
    │  }                      │                               │
```

---

## 5. Cache Data Flow

```
[Query Request]
    │
    ├── Check query cache (Redis)
    │       Key: sha256(query + filters + config)
    │       TTL: 60 min
    │       ├── HIT  ────────────────────────────► Return cached response
    │       └── MISS ────────────────────────────► Continue pipeline
    │
    ├── Check embedding cache (Redis)
    │       Key: sha256(text + model_id)
    │       TTL: 24 hours
    │       ├── HIT  ────────────────────────────► Use cached vector
    │       └── MISS ────────────────────────────► Call embedding API → cache
    │
    └── After pipeline completes:
            Store in query cache (Redis)
            Store embedding vectors in cache (Redis)
```

---

## 6. Database Write Paths Summary

| Event | PostgreSQL | Qdrant | Elasticsearch | Redis |
|-------|-----------|--------|---------------|-------|
| Document ingested | documents, chunks, metadata | vectors + payload | BM25 index | invalidate query cache |
| Query processed | conversations, messages | — | — | update query cache |
| Feedback received | feedback | — | — | — |
| Evaluation run | evaluation_runs, metrics | — | — | — |
| User login | sessions | — | — | session token |
| Config updated | settings | — | — | invalidate all caches |
