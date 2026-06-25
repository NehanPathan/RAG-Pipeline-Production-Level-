# Folder Structure — Enterprise Agentic RAG Platform

## Complete Project Layout

```
prod_rag/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                    ← pytest, ruff, mypy on PR
│   │   ├── cd.yml                    ← build + push Docker on merge to main
│   │   └── eval.yml                  ← scheduled RAGAS eval (weekly)
│   └── PULL_REQUEST_TEMPLATE.md
│
├── docs/
│   ├── architecture/                 ← all architecture docs (this folder)
│   ├── api/                          ← OpenAPI spec exports
│   └── runbooks/                     ← operational runbooks
│
├── src/
│   ├── __init__.py
│   │
│   ├── api/                          ← FastAPI presentation layer
│   │   ├── __init__.py
│   │   ├── main.py                   ← FastAPI app factory
│   │   ├── dependencies.py           ← get_query_pipeline(): manual singleton factory
│   │   │                                wiring all of Modules A-G (Phase 3); not the
│   │   │                                `dependency-injector` package — see design review §7
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py               ← JWT validation
│   │   │   ├── rate_limit.py         ← Redis sliding window
│   │   │   ├── request_id.py         ← X-Request-ID header
│   │   │   └── tracing.py            ← OTel span injection
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── auth.py               ← POST /auth/token, /auth/refresh
│   │       ├── documents.py          ← CRUD /documents
│   │       ├── chat.py               ← POST /chat (SSE stream or JSON; wired to QueryPipeline)
│   │       ├── retrieval.py          ← POST /retrieval/inspect (wired to QueryPipeline)
│   │       ├── evaluation.py         ← POST /eval/run, GET /eval/results
│   │       ├── feedback.py           ← POST /feedback
│   │       ├── admin.py              ← GET/PUT /admin/settings
│   │       └── health.py             ← GET /health, /metrics
│   │
│   ├── domain/                       ← Pure domain logic, no framework deps
│   │   ├── __init__.py
│   │   ├── entities/
│   │   │   ├── __init__.py
│   │   │   ├── document.py           ← Document, DocumentChunk
│   │   │   ├── conversation.py       ← Conversation, Message
│   │   │   ├── evaluation.py         ← EvaluationRun, EvalMetric
│   │   │   └── feedback.py           ← UserFeedback
│   │   ├── value_objects/             ← Citation lives in entities/conversation.py, not here
│   │   │   ├── __init__.py
│   │   │   ├── query_intent.py       ← IntentType, QueryIntent (Phase 3)
│   │   │   ├── metadata_filter.py    ← MetadataFilterSpec + to_vector_filter()/to_bm25_filter() (Phase 3)
│   │   │   ├── processed_query.py    ← ProcessedQuery, Query Intelligence Layer output (Phase 3)
│   │   │   ├── retrieval_trace.py    ← RetrievalTrace, per-stage latency/count breakdown (Phase 3)
│   │   │   ├── retrieval_candidate.py← FusedChunk, RerankedChunk (Phase 3)
│   │   │   ├── cache_entry.py        ← SemanticCacheEntry (Phase 3)
│   │   │   └── context_bundle.py     ← CompressedChunk, AssembledContext (Phase 3)
│   │   ├── repositories/             ← Abstract interfaces only
│   │   │   ├── __init__.py
│   │   │   ├── document_repository.py
│   │   │   ├── vector_repository.py  ← also defines ScoredChunk, VectorSearchFilter
│   │   │   ├── search_repository.py  ← also defines BM25ScoredChunk, BM25SearchFilter
│   │   │   ├── cache_repository.py   ← SemanticCacheRepository ABC (Phase 3)
│   │   │   ├── conversation_repository.py  ← not yet built (Phase 4)
│   │   │   └── evaluation_repository.py    ← not yet built (Phase 6)
│   │   └── services/                 ← not yet built; RRF/citation logic lives in
│   │                                    retrieval/fusers/ and retrieval/context/ instead
│   │                                    (Phase 3 design review §5: pure algorithmic
│   │                                    utilities don't need a domain/services/ home
│   │                                    when they have no real domain-entity behavior)
│   │
│   ├── application/                  ← Use cases, DTOs, orchestration
│   │   ├── __init__.py
│   │   ├── use_cases/
│   │   │   ├── __init__.py
│   │   │   ├── ingest_document.py    ← not yet built; IngestionPipeline is called
│   │   │   │                            directly from documents.py today
│   │   │   ├── delete_document.py    ← not yet built
│   │   │   ├── process_query.py      ← ProcessQueryUseCase (Phase 3) — thin wrapper
│   │   │   │                            around QueryPipeline.answer(); no separate
│   │   │   │                            StreamAnswerUseCase needed, since answer() is
│   │   │   │                            already the streaming entry point
│   │   │   ├── inspect_retrieval.py  ← InspectRetrievalUseCase (Phase 3)
│   │   │   ├── run_evaluation.py     ← not yet built (Phase 6)
│   │   │   └── record_feedback.py    ← not yet built (Phase 6)
│   │   ├── dtos/
│   │   │   ├── __init__.py
│   │   │   ├── document_dto.py
│   │   │   ├── query_dto.py
│   │   │   ├── response_dto.py
│   │   │   └── evaluation_dto.py
│   │   └── mappers/
│   │       ├── __init__.py
│   │       └── document_mapper.py
│   │
│   ├── infrastructure/               ← Concrete implementations
│   │   ├── __init__.py
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   ├── postgres/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── connection.py     ← SQLAlchemy async engine
│   │   │   │   ├── models.py         ← ORM models
│   │   │   │   ├── migrations/       ← Alembic migrations
│   │   │   │   │   ├── env.py
│   │   │   │   │   └── versions/
│   │   │   │   └── repositories/
│   │   │   │       ├── document_repo.py
│   │   │   │       ├── conversation_repo.py
│   │   │   │       └── evaluation_repo.py
│   │   │   └── redis/
│   │   │       ├── __init__.py
│   │   │       ├── connection.py
│   │   │       └── cache.py
│   │   ├── vector_store/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← VectorRepository ABC
│   │   │   ├── qdrant/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── client.py
│   │   │   │   └── repository.py     ← QdrantVectorRepository
│   │   │   ├── weaviate/             ← Optional
│   │   │   │   └── repository.py
│   │   │   └── pinecone/             ← Optional
│   │   │       └── repository.py
│   │   └── search/
│   │       ├── __init__.py
│   │       ├── base.py               ← SearchRepository ABC
│   │       └── elasticsearch/
│   │           ├── __init__.py
│   │           ├── client.py
│   │           └── repository.py     ← ESSearchRepository
│   │
│   ├── ingestion/                    ← Document processing pipeline
│   │   ├── __init__.py
│   │   ├── loaders/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← DocumentLoader ABC
│   │   │   ├── docling_loader.py
│   │   │   ├── unstructured_loader.py
│   │   │   └── llamaparse_loader.py
│   │   ├── extractors/
│   │   │   ├── __init__.py
│   │   │   ├── text_extractor.py
│   │   │   ├── table_extractor.py
│   │   │   └── image_ref_extractor.py
│   │   ├── cleaners/
│   │   │   ├── __init__.py
│   │   │   └── document_cleaner.py
│   │   ├── enrichers/
│   │   │   ├── __init__.py
│   │   │   ├── metadata_extractor.py ← File-level metadata
│   │   │   └── llm_enricher.py       ← LLM-based enrichment
│   │   ├── chunkers/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← Chunker ABC
│   │   │   ├── semantic_chunker.py
│   │   │   ├── token_chunker.py
│   │   │   └── parent_child_chunker.py
│   │   ├── embedders/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← EmbeddingProvider ABC
│   │   │   ├── openai_embedder.py
│   │   │   ├── bge_m3_embedder.py
│   │   │   └── e5_large_embedder.py
│   │   └── pipeline.py               ← IngestionPipeline orchestrator
│   │
│   ├── retrieval/                    ← Query processing pipeline (Phase 3 — see
│   │   │                                docs/architecture/11_phase3_design_review.md
│   │   │                                for the full module-by-module build log)
│   │   ├── __init__.py
│   │   ├── agents/                   ← Module A: Query Intelligence Layer
│   │   │   ├── __init__.py
│   │   │   ├── query_rewriter.py
│   │   │   ├── query_expander.py
│   │   │   ├── intent_classifier.py
│   │   │   ├── source_selector.py    ← deterministic, not LLM-based (see design review)
│   │   │   ├── filter_generator.py
│   │   │   └── query_agent.py        ← orchestrates all 5 above
│   │   ├── searchers/                ← Module B: Retrieval Layer
│   │   │   ├── __init__.py
│   │   │   ├── vector_searcher.py
│   │   │   └── bm25_searcher.py
│   │   ├── hybrid_retriever.py       ← Module B orchestrator: runs both searchers
│   │   │                                concurrently, builds RetrievalTrace
│   │   ├── fusers/                   ← Module C: Fusion Layer
│   │   │   ├── __init__.py
│   │   │   ├── rrf_fuser.py
│   │   │   ├── score_normalizer.py
│   │   │   ├── duplicate_remover.py  ← exact content-hash dedup
│   │   │   └── fuser.py              ← orchestrates RRF -> normalize -> dedup
│   │   ├── rerankers/                ← Module D: Reranking Layer
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← Reranker ABC
│   │   │   ├── bge_reranker.py
│   │   │   ├── cohere_reranker.py
│   │   │   └── registry.py           ← get_reranker(settings)
│   │   ├── context/                  ← Module E: Context Processing Layer
│   │   │   ├── __init__.py
│   │   │   ├── token_counter.py
│   │   │   ├── token_budget_manager.py
│   │   │   ├── context_deduplicator.py  ← containment dedup, distinct from
│   │   │   │                              fusers/duplicate_remover.py's exact-hash dedup
│   │   │   ├── context_compressor.py
│   │   │   ├── citation_preserver.py
│   │   │   └── context_processor.py  ← orchestrates dedup -> compress -> budget -> citations
│   │   ├── cache/                    ← Module F: Semantic Cache Layer
│   │   │   ├── __init__.py
│   │   │   └── semantic_cache.py
│   │   ├── answer/                   ← Module G: Answer Pipeline
│   │   │   ├── __init__.py
│   │   │   ├── context_assembler.py
│   │   │   ├── prompt_builder.py
│   │   │   ├── stream_generator.py
│   │   │   ├── citation_validator.py
│   │   │   └── answer_pipeline.py    ← async-generator orchestrator, yields SSE events
│   │   └── pipeline.py               ← QueryPipeline: ties Modules A-G together;
│   │                                    inspect() for /retrieval/inspect (A-D only),
│   │                                    answer() for /chat (cache -> A-D -> E -> G)
│   │
│   ├── llm/                          ← LLM provider abstraction (Phase 3 Module 0b)
│   │   ├── __init__.py
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               ← LLMProvider ABC
│   │   │   └── openai_provider.py    ← only provider implemented so far;
│   │   │                                anthropic/gemini/ollama are Phase 4
│   │   ├── json_parsing.py           ← parse_json_response(), shared by Module A agents
│   │   ├── prompts/                  ← not yet used; prompts currently live as
│   │   │   └── __init__.py             module-level constants next to their consumer
│   │   │                                (matches the existing llm_enricher.py convention)
│   │   └── registry.py               ← get_llm_provider(settings, role="small"|"large")
│   │
│   ├── evaluation/                   ← Eval framework
│   │   ├── __init__.py
│   │   ├── offline/
│   │   │   ├── __init__.py
│   │   │   ├── ragas_evaluator.py
│   │   │   └── deepeval_evaluator.py
│   │   ├── online/
│   │   │   ├── __init__.py
│   │   │   ├── feedback_service.py
│   │   │   └── latency_tracker.py
│   │   └── metrics.py                ← Metric definitions
│   │
│   ├── monitoring/                   ← Observability
│   │   ├── __init__.py
│   │   ├── langfuse_tracer.py
│   │   ├── otel_tracer.py
│   │   ├── prometheus_metrics.py
│   │   └── logger.py                 ← structlog setup
│   │
│   └── ui/                           ← Streamlit application
│       ├── __init__.py
│       ├── app.py                    ← Streamlit entry point
│       ├── pages/
│       │   ├── 01_chat.py
│       │   ├── 02_documents.py
│       │   ├── 03_retrieval_inspector.py
│       │   ├── 04_evaluation.py
│       │   └── 05_admin.py
│       ├── components/
│       │   ├── __init__.py
│       │   ├── chat_message.py       ← Message + citation renderer
│       │   ├── document_card.py
│       │   ├── score_table.py
│       │   └── metric_chart.py
│       └── api_client.py             ← HTTP client for FastAPI
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   ← Shared fixtures, test DB setup
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── domain/
│   │   │   ├── test_chunking_service.py
│   │   │   ├── test_ranking_service.py
│   │   │   └── test_citation_service.py
│   │   ├── ingestion/
│   │   │   ├── test_semantic_chunker.py
│   │   │   ├── test_parent_child_chunker.py
│   │   │   └── test_rrf_fuser.py
│   │   └── retrieval/
│   │       ├── test_query_rewriter.py
│   │       └── test_intent_classifier.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_ingestion_pipeline.py
│   │   ├── test_query_pipeline.py
│   │   ├── test_qdrant_repository.py
│   │   ├── test_es_repository.py
│   │   └── test_postgres_repository.py
│   └── e2e/
│       ├── __init__.py
│       ├── test_chat_flow.py
│       └── test_document_lifecycle.py
│
├── docker/
│   ├── api.Dockerfile
│   ├── streamlit.Dockerfile
│   └── nginx.conf                    ← Reverse proxy config
│
├── scripts/
│   ├── seed_eval_dataset.py
│   ├── migrate.py
│   └── create_admin.py
│
├── docker-compose.yml
├── docker-compose.dev.yml            ← Dev overrides (hot reload)
├── .env.example
├── Makefile
├── pyproject.toml                    ← uv/poetry project config
├── ruff.toml                         ← Linter config
├── mypy.ini                          ← Type checker config
└── alembic.ini
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `src/` layout | Prevents import confusion, clear boundary |
| Separate `domain/repositories/` (abstract) from `infrastructure/` (concrete) | Clean Architecture: domain never imports infrastructure |
| `pipeline.py` as orchestrator per domain | Single entry point per pipeline, easy to test |
| `providers/base.py` ABCs in `ingestion/`, `llm/`, `retrieval/` | Swap providers without touching business logic |
| `ui/api_client.py` as thin HTTP wrapper | Streamlit calls FastAPI — no shared code between UI and backend |
| `tests/` mirrors `src/` structure | Easy to find tests for any module |
