# Build Roadmap — Enterprise Agentic RAG Platform

## Timeline Overview

```
Week 1-2   │ Phase 1: Foundation
Week 3-4   │ Phase 2: Ingestion Pipeline
Week 5-6   │ Phase 3: Retrieval Pipeline
Week 7-8   │ Phase 4: Agentic Query Processing + Answer Generation
Week 9     │ Phase 5: Streamlit UI
Week 10    │ Phase 6: Evaluation Framework
Week 11    │ Phase 7: Observability + Security Hardening
Week 12    │ Phase 8: Testing, Deployment, Production Readiness
```

---

## Phase 1: Foundation (Week 1–2)

### Milestone: Project skeleton + infrastructure running

**Tasks:**
- [ ] Initialize project structure (all directories, `__init__.py` files)
- [ ] `pyproject.toml` with all dependencies pinned
- [ ] `docker-compose.yml` with Postgres, Qdrant, Elasticsearch, Redis
- [ ] Alembic setup + initial migration (users, api_keys, documents, settings tables)
- [ ] FastAPI app factory with health endpoint
- [ ] Dependency injection container (`dependency-injector`)
- [ ] Structured logging with `structlog`
- [ ] Ruff + mypy + pytest configuration
- [ ] GitHub Actions CI (lint + type check + test on PR)
- [ ] `.env.example` with all required variables
- [ ] `Makefile` with `make dev`, `make test`, `make migrate`, `make build`
- [ ] Domain entities + value objects (Document, Chunk, Conversation, etc.)
- [ ] Repository interfaces (abstract base classes)

**Deliverables:**
- `docker-compose up` runs all services
- `GET /health` returns 200 with all dependencies green
- CI pipeline passing

---

## Phase 2: Ingestion Pipeline (Week 3–4)

### Milestone: Upload any document → searchable in Qdrant + ES

**Tasks:**
- [ ] `DocumentLoader` ABC + Docling implementation (PDF, DOCX)
- [ ] `UnstructuredLoader` implementation (HTML, TXT, MD)
- [ ] `ContentExtractor`: text blocks, table extraction, image references
- [ ] `DocumentCleaner`: whitespace normalization, boilerplate removal
- [ ] `MetadataExtractor`: file-level metadata
- [ ] `LLMMetadataEnricher`: summary, tags, domain, entities via small LLM
- [ ] `SemanticChunker`: embedding-based sentence boundary chunking
- [ ] `ParentChildChunker`: 1024-token parents + 256-token children
- [ ] `EmbeddingGenerator`: OpenAI + BGE-M3 implementations with batch processing
- [ ] Redis embedding cache
- [ ] `QdrantRepository`: collection creation, upsert, delete
- [ ] `ElasticsearchRepository`: index creation, bulk index, delete
- [ ] `PostgresDocumentRepository`: CRUD for documents + chunks
- [ ] `IngestionPipeline` orchestrator (async, step-by-step with progress)
- [ ] `IngestDocumentUseCase`
- [ ] `POST /documents` and `GET /documents/{id}` endpoints
- [ ] Async background task with Redis status tracking
- [ ] Unit tests: chunkers, cleaner, metadata extractor
- [ ] Integration test: full ingestion of sample PDF

**Deliverables:**
- Upload a PDF → chunks in Qdrant + ES + PostgreSQL
- Status endpoint shows progress
- 80%+ test coverage on ingestion module

---

## Phase 3: Query Intelligence and Retrieval Pipeline (Week 5–8) — DONE

### Milestone: Query → streamed, cited answer, full hybrid retrieval pipeline

This phase's scope was expanded mid-build to absorb most of what this roadmap originally
split out as "Phase 4: Agentic Query + Answer Generation" below. See
`11_phase3_design_review.md` for the full design review, gap analysis, and a
module-by-module build log — what was built, why it looks the way it does, and what was
found/fixed along the way.

**Tasks:**
- [x] `LLMProvider` ABC + `OpenAIProvider` (Anthropic/Gemini/Ollama: ABC ready, not yet implemented)
- [x] `QueryRewriter`, `QueryExpander`, `IntentClassifier`, `SourceSelector`, `FilterGenerator`, `QueryAgent` orchestrator
- [x] `VectorSearcher`, `BM25Searcher`: query fan-out + merge-by-max-score, parallel via `asyncio.gather`
- [x] `RRFFusion`, `ScoreNormalizer`, `DuplicateRemover` (content-hash dedup)
- [x] `BGEReranker`, `CohereReranker`, reranker registry
- [x] `TokenCounter`, `TokenBudgetManager`, `ContextCompressor`, `ContextDeduplicator` (containment dedup), `CitationPreserver`
- [x] `SemanticCacheRepository` ABC + Qdrant-backed implementation, `SemanticCache` service
- [x] `ContextAssembler`, `PromptBuilder`, `StreamGenerator`, `CitationValidator`, `AnswerPipeline`
- [x] `QueryPipeline` orchestrator (`inspect()` for debug, `answer()` for `/chat`)
- [x] `ProcessQueryUseCase`, `InspectRetrievalUseCase`
- [x] `POST /chat` wired to the real pipeline (SSE + non-streaming JSON modes)
- [x] `POST /retrieval/inspect` endpoint, full debug payload
- [x] OpenTelemetry + Langfuse spans for every retrieval stage (`traced_stage()` helper)
- [x] Unit tests for every module (184 tests across Modules 0/A–G + Final Integration)
- [ ] Integration tests against real Qdrant/ES/Redis (deferred — unit tests came first per requirement; follow-up)
- [ ] Conversation persistence to PostgreSQL (deferred — no `ConversationRepository` yet; `/chat` generates and streams real answers but doesn't save them — see Phase 4 below)

**Found and fixed along the way (not originally scoped here, but blocking):**
- Multi-tenant isolation was silently non-functional in `QdrantVectorRepository`/`ElasticsearchSearchRepository` (Phase 2) — `user_id`/`domain`/`tags`/`file_type` were never written to the payload despite filters already being built against those keys.
- Both repositories' `search()` reconstruction dropped `page_number`/`section`/`contains_table`/`parent_chunk_id`, and (after the fix above) the new tenant fields too — citations need these on data coming back *from* a search call, not just at ingest time.
- `AsyncQdrantClient.search()` doesn't exist in the installed `qdrant-client` (1.16.2) — replaced by `query_points()`. This had been silently broken since whenever qdrant-client was last upgraded; no test caught it because every Qdrant test mocked the client with a bare `AsyncMock()`. Test fixtures now use `AsyncMock(spec=AsyncQdrantClient)` to catch this class of bug going forward.

**Deliverables:**
- `POST /retrieval/inspect` returns full debug payload (all scores at every stage) ✅
- Parallel retrieval working ✅
- `POST /chat` streams answer with citations ✅
- 184/184 unit tests passing

---

## Phase 4: Multi-Provider LLM + Conversation Persistence (Week 7–8) — NARROWED

### Milestone: Additional LLM providers, conversation history persisted

Most of this phase's original scope (`QueryAgent`, `ContextCompressor`, answer generation
with streaming + citations, `ProcessQueryUseCase`, `POST /chat`) was completed early, folded
into Phase 3 above. What's actually left:

**Tasks:**
- [ ] `AnthropicProvider`, `GeminiProvider`, `OllamaProvider` — implementing the existing `LLMProvider` ABC; no interface changes needed, `get_llm_provider()` just gains branches
- [ ] A real `LLMProviderRegistry` once there's a 3rd+ provider (today `get_llm_provider()` is a 2-branch function — not worth a registry abstraction for one provider)
- [ ] `ConversationRepository` (domain interface + Postgres implementation — `ConversationModel`/`MessageModel` ORM tables already exist from Phase 1)
- [ ] Wire conversation persistence into `/chat` (save `Message`/`Conversation` after each turn)
- [ ] Auth-derived `user_id` for `/chat`/`/retrieval/inspect` (currently a request-body stand-in field; pending Phase 7's JWT middleware)
- [ ] Integration test: end-to-end query on indexed corpus (real Qdrant/ES/Redis)

---

## Phase 5: Streamlit UI (Week 9)

> **Superseded.** This phase is left as written because it records what was
> actually built and why. The Streamlit UI did its job -- it proved the API
> surface before any React was written -- and the React app under
> `frontend/` has since overtaken it on every page, adding drawings,
> revisions, ingest and access management. Streamlit is now gated behind
> the `debug` compose profile. See `docs/SECURITY.md` §3 for how each UI
> obtains a token.

### Milestone: Functional UI covering all 5 pages

**Tasks:**
- [ ] `ui/api_client.py`: async HTTP client wrapping FastAPI
- [ ] Page 1 — Chat: streaming SSE consumption, citation rendering, conversation history sidebar
- [ ] Page 2 — Document Management: upload form, status polling, metadata viewer, delete/reindex
- [ ] Page 3 — Retrieval Inspector: query input, score comparison table, chunk viewer
- [ ] Page 4 — Evaluation Dashboard: metric cards, trend charts (Altair/Plotly), feedback summary
- [ ] Page 5 — Admin Panel: model selection dropdowns, retrieval settings sliders
- [ ] Shared components: chat message renderer, citation badge, score table, metric chart
- [ ] Authentication: API key input, session management
- [ ] Error handling: user-friendly messages for API errors
- [ ] Responsive layout with Streamlit columns

**Deliverables:**
- All 5 pages functional and connected to FastAPI
- End-to-end user flow testable via UI

---

## Phase 6: Evaluation Framework (Week 10)

### Milestone: RAGAS evaluation runnable and results persisted

**Tasks:**
- [ ] `RAGASEvaluator`: faithfulness, answer_relevancy, context_precision, context_recall
- [ ] `DeepEvalEvaluator`: hallucination score
- [ ] Sample golden dataset (50 questions with ground truth)
- [ ] `FeedbackService`: record thumbs up/down, tags
- [ ] `LatencyTracker`: TTFB, streaming duration, total latency
- [ ] `RunEvaluationUseCase`, `RecordFeedbackUseCase`
- [ ] `POST /evaluation/runs`, `GET /evaluation/runs/{id}`
- [ ] `POST /feedback`
- [ ] Prometheus metrics export for evaluation scores
- [ ] Scheduled evaluation (GitHub Actions weekly cron)
- [ ] Unit tests: metric computation
- [ ] Integration test: full eval run with mock LLM

**Deliverables:**
- Evaluation runs and stores RAGAS + DeepEval scores
- Feedback endpoint working
- Scores visible in Streamlit Evaluation Dashboard

---

## Phase 7: Observability + Security (Week 11)

### Milestone: Full tracing, metrics, and security hardening

**Tasks:**
- [ ] Langfuse integration: trace every query with spans per pipeline step
- [ ] OpenTelemetry: HTTP spans, DB spans, external API spans
- [ ] Prometheus metrics: all defined counters/histograms/gauges
- [ ] `GET /metrics` Prometheus endpoint
- [ ] `structlog` JSON logging with request_id, user_id, trace_id
- [ ] JWT authentication middleware (issue + verify)
- [ ] API key authentication middleware
- [ ] RBAC enforcement on all protected routes
- [ ] Rate limiting middleware (Redis sliding window)
- [ ] CORS configuration
- [ ] Input validation hardening (file type, size, content-type)
- [ ] `POST /auth/token`, `POST /auth/refresh`, `DELETE /auth/logout`
- [ ] Admin API key management endpoints
- [ ] Security audit: verify tenant isolation in all queries

**Deliverables:**
- Every query visible in Langfuse with full span breakdown
- Prometheus dashboard showing all metrics
- Security: auth + RBAC + rate limiting on all endpoints

---

## Phase 8: Testing + Deployment (Week 12)

### Milestone: 90%+ test coverage + production Docker deployment

**Tasks:**
- [ ] Complete unit test suite (90%+ coverage target)
- [ ] Integration tests: all repository implementations against real services
- [ ] E2E tests: full document lifecycle + full chat flow
- [ ] `pytest-cov` coverage report in CI
- [ ] `api.Dockerfile` and `streamlit.Dockerfile` (multi-stage, non-root user)
- [ ] `docker-compose.yml` production configuration
- [ ] Nginx reverse proxy config
- [ ] GitHub Actions CD: build + push images on merge to main
- [ ] `Makefile` targets: `make deploy`, `make rollback`
- [ ] Production readiness checklist (doc)
- [ ] Performance test: 100 concurrent queries (k6 or locust)
- [ ] Load test: 1,000 document ingestion run

**Deliverables:**
- `docker-compose up` starts complete production stack
- CI/CD pipeline fully automated
- Test coverage ≥ 90%
- All performance targets met

---

## GitHub Milestones

| Milestone | Target Date | Success Criteria |
|-----------|-------------|-----------------|
| M1: Foundation | Week 2 | `make dev` starts all services, CI passing |
| M2: Ingestion | Week 4 | PDF upload → searchable in Qdrant+ES |
| M3: Retrieval | Week 6 | `/retrieval/inspect` returns full debug payload |
| M4: Query+Answer | Week 8 | `/chat` streams answer with citations |
| M5: UI | Week 9 | All 5 Streamlit pages functional |
| M6: Evaluation | Week 10 | RAGAS eval runs + results persisted |
| M7: Security+Obs | Week 11 | Auth + Langfuse traces + Prometheus metrics |
| M8: Ship | Week 12 | 90% test coverage + production Docker |

---

## Technology Stack (Final)

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.12+ |
| API Framework | FastAPI | 0.115+ |
| UI | Streamlit | 1.40+ |
| ORM | SQLAlchemy (async) | 2.0+ |
| Migrations | Alembic | 1.13+ |
| DI Container | dependency-injector | 4.41+ |
| Logging | structlog | 24.4+ |
| Vector DB | Qdrant | 1.12+ |
| Search | Elasticsearch | 8.x |
| Cache/Sessions | Redis | 7.x |
| Relational DB | PostgreSQL | 16 |
| Document Parsing | Docling | latest |
| Document Parsing | Unstructured | 0.16+ |
| LLM - Anthropic | anthropic | 0.40+ |
| LLM - OpenAI | openai | 1.50+ |
| LLM - Google | google-generativeai | 0.8+ |
| LLM - Local | ollama | latest |
| Evaluation | RAGAS | 0.2+ |
| Evaluation | DeepEval | 1.4+ |
| Observability | Langfuse | 2.x |
| Observability | opentelemetry-sdk | 1.x |
| Metrics | prometheus-client | 0.21+ |
| Reranker | sentence-transformers | 3.x |
| Package Manager | uv | latest |
| Linting | ruff | 0.8+ |
| Type Checking | mypy | 1.10+ |
| Testing | pytest + pytest-asyncio | latest |
| Containerization | Docker + Docker Compose | v2 |
