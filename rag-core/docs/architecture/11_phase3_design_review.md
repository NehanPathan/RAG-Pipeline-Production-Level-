# Phase 3 Design Review & Gap Analysis — Query Intelligence and Retrieval Pipeline

## 1. Scope

Phase 3 covers the full query path from raw user question to a streamed, cited answer:

```
A. Query Intelligence  → B. Retrieval  → C. Fusion  → D. Reranking
→ E. Context Processing → F. Semantic Cache → G. Answer Pipeline
```

This supersedes the narrower "Phase 3: Retrieval Pipeline" scope in `10_build_roadmap.md`
(which stopped at reranking) by absorbing most of what was previously scoped as
"Phase 4: Agentic Query + Answer Generation". `10_build_roadmap.md` will be updated to
reflect this merge once the design below is approved.

---

## 2. What Already Exists (Reusable As-Is)

| Layer | Asset | Notes |
|---|---|---|
| Domain | `Document`, `DocumentChunk`, `ChunkType`, `Conversation`, `Message`, `Citation` (`domain/entities/`) | Stable, reusable without changes |
| Domain | `VectorRepository` ABC + `VectorSearchFilter` + `ScoredChunk` (`domain/repositories/vector_repository.py`) | Reusable; filter shape needs extension (see Gap 4) |
| Domain | `SearchRepository` ABC + `BM25SearchFilter` + `BM25ScoredChunk` (`domain/repositories/search_repository.py`) | Same as above |
| Domain | `ChunkRepository.get_by_ids` (`domain/repositories/document_repository.py`) | Needed for parent-chunk expansion in context assembly |
| Infra | `QdrantVectorRepository`, `ElasticsearchSearchRepository` | Fully implemented, filter-building pattern to follow |
| Infra | `RedisCache` (`infrastructure/database/redis/connection.py`) | Generic get/set/delete/pattern-delete — reusable for semantic cache storage |
| Infra | `OpenAIEmbeddingProvider` + `EmbeddingProvider` ABC | Reusable for query embedding (vector search + semantic cache) |
| Config | `src/config.py` | Already has `vector_search_top_k`, `bm25_search_top_k`, `rerank_top_n`, `rrf_k`, `reranker_provider`, `cohere_*`, `bge_*`, LLM provider/model settings |
| Observability | `structlog` logger, Prometheus registry/metrics | Reusable; tracing/Langfuse not yet wired (Gap 6) |
| Orchestration pattern | `IngestionPipeline` (`ingestion/pipeline.py`) | Template to follow for the new `QueryPipeline` orchestrator — constructor-injected dependencies, step-by-step try/except, structured logging |
| Testing pattern | `tests/conftest.py` mock fixtures (`AsyncMock` repos) | Template for new retrieval fixtures |
| API | `POST /api/v1/chat` (SSE stub), `07_api_design.md` `/retrieval/inspect` contract | Defines the exact target payload shape Phase 3 must produce |

---

## 3. Gap Analysis

### Gap 1 — No LLM Provider abstraction exists (blocking)
`src/llm/providers/` contains only `__init__.py`. Yet `LLMMetadataEnricher`
(`ingestion/enrichers/llm_enricher.py`) already calls `self._llm.complete(prompt=..., max_tokens=..., temperature=...)` — an interface that was assumed but never defined. Query Intelligence (intent detection, rewriting, expansion, filter generation) and the Answer Pipeline both require an LLM call. **Nothing in Module A can be built without this.**

**Resolution:** Insert a small prerequisite "Module 0" before Module A: an `LLMProvider` ABC matching the signature `LLMMetadataEnricher` already relies on, plus one concrete provider. Interface:

```python
class LLMProvider(ABC):
    async def complete(self, prompt: str, max_tokens: int, temperature: float) -> str: ...
    async def stream(self, prompt: str, max_tokens: int, temperature: float) -> AsyncIterator[str]: ...
    @property
    def model_id(self) -> str: ...
```

`stream()` is added now (not in the original enricher contract) because Module G needs token-by-token generation. Both small and large LLM roles are just two instances of the same ABC, selected via `settings.small_llm_provider` / `settings.large_llm_provider`.

### Gap 2 — No domain value objects for query/retrieval concepts
`domain/value_objects/` is empty. Concepts needed for Phase 3 (`QueryIntent`, `ProcessedQuery`, fused/reranked chunk wrappers, retrieval trace, metadata filter spec) have no home. Precedent in this codebase (`ScoredChunk` living inside `vector_repository.py`) mixes domain concepts into repository-interface files — acceptable for Phase 2's narrow case, but Phase 3 has too many cross-stage value objects for that to scale.

**Resolution:** Create proper files under `domain/value_objects/` per the original `05_folder_structure.md` plan: `query_intent.py`, `processed_query.py`, `metadata_filter.py`, `retrieval_candidate.py` (`FusedChunk`, `RerankedChunk`), `retrieval_trace.py`, `context_bundle.py`, `cache_entry.py`. Existing `ScoredChunk`/`BM25ScoredChunk` stay where they are (no breaking changes to Phase 2 code) — Fusion layer consumes them and produces the new `FusedChunk`.

### Gap 3 — Metadata filter shape is too narrow for LLM-generated filters
`VectorSearchFilter`/`BM25SearchFilter` only support `user_id, domain, tags, file_type, document_ids`. The Metadata Filter Generation sub-module (LLM-driven) should be able to express richer constraints (date ranges, arbitrary key/value) without forcing Qdrant/ES repository changes.

**Resolution:** Introduce `MetadataFilterSpec` (domain value object, superset shape) produced by the Query Intelligence Layer, with adapter methods `to_vector_filter()` / `to_bm25_filter()` that project onto the existing repository-specific filter dataclasses. Repository interfaces stay untouched — only an adapter is added. Date-range filtering beyond what Qdrant/ES already index will be marked as a documented limitation rather than forcing a payload-index migration mid-phase.

### Gap 4 — No Reranker abstraction
`retrieval/rerankers/` is empty. Roadmap calls for a `Reranker` ABC with `BGEReranker` (local cross-encoder via `sentence-transformers`, already a pinned dependency) and `CohereReranker` (API-based, `cohere` already pinned), selected via `settings.reranker_provider`.

**Resolution:**
```python
class Reranker(ABC):
    async def rerank(self, query: str, candidates: list[FusedChunk], top_n: int) -> list[RerankedChunk]: ...
    @property
    def name(self) -> str: ...
```
A simple factory function `get_reranker(settings) -> Reranker` replaces a full registry class — no registry abstraction is justified for two providers.

### Gap 5 — No Semantic Cache repository abstraction
Plain Redis (`redis:7-alpine`, confirmed in `docker-compose.yml`) has no vector/ANN module (no RediSearch/Redis Stack). True `O(1)` vector similarity search isn't available inside Redis as configured.

**Resolution (revised, see §6):** Define a domain-level `SemanticCacheRepository` ABC (consistent with the existing Repository pattern) with `find_similar(embedding, threshold) -> SemanticCacheEntry | None` and `store(entry, ttl) -> None`. The concrete implementation is **Qdrant-backed** (`QdrantSemanticCacheRepository`), using a dedicated cache collection separate from `document_chunks`, giving native ANN similarity search instead of a bounded client-side scan. The interface is what matters for dependency inversion — the implementation could be swapped for a Redis-Stack/RediSearch-backed one later without touching call sites.

### Gap 6 — No tracing/observability foundation
`monitoring/` has `logger.py` and `prometheus_metrics.py` only. No OpenTelemetry span setup, no Langfuse client wrapper exist anywhere, despite both being pinned dependencies (`opentelemetry-sdk`, `langfuse`). The task requires OTel tracing **and** a Langfuse span **per retrieval stage** — this is cross-cutting and touches every module in A–G.

**Resolution:** Build this once, first, as the actual "Module 0": `monitoring/tracing.py` (OTel tracer + span helper) and `monitoring/langfuse_tracer.py` (Langfuse client + generation/span helper), unified behind one context manager so every pipeline stage gets both with a single call:

```python
async with traced_stage("vector_search", query=query, top_k=20) as span:
    results = await vector_repo.search(...)
    span.set_result(count=len(results))
```

Every module from here on uses this helper instead of inventing its own tracing calls.

### Gap 7 — No `QueryPipeline` orchestrator or use cases
`application/use_cases/` and `retrieval/` subpackages are empty beyond `__init__.py`. `chat.py` currently stubs the whole pipeline inline in the route. There's no equivalent of `IngestionPipeline` for the query side, and no `InspectRetrievalUseCase`/`ProcessQueryUseCase`.

**Resolution:** Mirror the ingestion pattern: build a `QueryPipeline` orchestrator (`retrieval/pipeline.py`) once stages A–F exist, then thin `application/use_cases/inspect_retrieval.py` and `process_query.py` wrapping it, then wire `/retrieval/inspect` and `/chat` to the use cases — replacing today's stub. This happens at the end of Phase 3, after the component modules are built and individually tested.

### Gap 8 — No `ConversationRepository` (noted, out of scope for Phase 3)
`domain/repositories/conversation_repository.py` doesn't exist yet, even though `ConversationModel`/`MessageModel` ORM tables already exist. Module G's listed scope (Context Assembly, Prompt Construction, Streaming Generation, Citation Validation) does not include conversation persistence. **Decision: persistence wiring for conversation history is explicitly deferred** — Module G produces an `AnswerResult` object; saving it to Postgres is a follow-up integration task, not blocked by anything in Phase 3.

### Gap 9 — Token budgeting utility missing
`tiktoken` is pinned but no token-counting helper exists anywhere. Needed by Module E (Token Budget Manager) and useful for Module G (prompt construction must stay under context window).

**Resolution:** Small stateless `TokenCounter` (wraps `tiktoken`, model-aware encoding lookup with a safe fallback) added in Module E, reused by Module G.

### Gap 10 — Settings surface needs extending
New config fields required across modules (added incrementally per-module, not all at once): query expansion count, rewrite/expansion toggles, intent confidence threshold, semantic cache toggle/threshold/TTL/max-candidates, context token budget, compression toggle, answer max tokens/temperature. All follow the existing `Settings` (`pydantic-settings`) pattern in `src/config.py`.

---

## 4. Proposed New Abstractions Summary

```
src/domain/value_objects/
    query_intent.py        ← IntentType enum, QueryIntent
    processed_query.py      ← ProcessedQuery (output of Query Intelligence Layer)
    metadata_filter.py       ← MetadataFilterSpec (+ to_vector_filter/to_bm25_filter adapters)
    retrieval_candidate.py   ← FusedChunk, RerankedChunk
    retrieval_trace.py       ← StageTiming, RetrievalTrace
    context_bundle.py        ← CompressedChunk, AssembledContext
    cache_entry.py           ← SemanticCacheEntry

src/domain/repositories/
    cache_repository.py      ← SemanticCacheRepository ABC (new)

src/llm/providers/
    base.py                  ← LLMProvider ABC (new)
    openai_provider.py       ← concrete impl (new)

src/monitoring/
    tracing.py                ← OTel span helper (new)
    langfuse_tracer.py        ← Langfuse span helper (new)

src/retrieval/
    agents/                   ← intent_classifier.py, query_rewriter.py, query_expander.py,
                                  source_selector.py, filter_generator.py, query_agent.py
    searchers/                ← vector_searcher.py, bm25_searcher.py
    fusers/                   ← rrf_fuser.py, score_normalizer.py, dedup.py
    rerankers/                ← base.py, bge_reranker.py, cohere_reranker.py
    context/                  ← token_budget_manager.py, compressor.py, dedup.py, citation_preserver.py
    cache/                    ← semantic_cache.py (Qdrant impl of SemanticCacheRepository)
    answer/                   ← context_assembler.py, prompt_builder.py, stream_generator.py, citation_validator.py
    pipeline.py                ← QueryPipeline orchestrator (built last)

src/application/use_cases/
    inspect_retrieval.py      ← (built last)
    process_query.py          ← (built last)
```

Most additions are net-new files. One exception, found while building Module B: `DocumentChunk` (`domain/entities/document.py`), `IngestionPipeline` (`ingestion/pipeline.py`), and both Phase 2 repositories (`QdrantVectorRepository`, `ElasticsearchSearchRepository`) needed small patches — see Module B's log entry in §7 for why. `05_folder_structure.md` will be updated to match once approved.

---

## 5. Build Order

```
Module 0a — Observability foundation (tracing.py + langfuse_tracer.py)
Module 0b — LLMProvider ABC + first concrete provider
Module A  — Query Intelligence Layer (Intent → Rewrite → Expand → Source Selection → Filter Gen → QueryAgent orchestrator)
Module B  — Retrieval Layer (VectorSearcher, BM25Searcher, parallel gather, RetrievalTrace)
Module C  — Fusion Layer (RRF, normalization, dedup)
Module D  — Reranking Layer (BGE, Cohere, factory)
Module E  — Context Processing (token budget, compression, dedup, citation preservation)
Module F  — Semantic Cache (Redis-backed SemanticCacheRepository)
Module G  — Answer Pipeline (context assembly, prompt construction, streaming generation, citation validation)
Final     — QueryPipeline orchestrator + use cases + wire /chat and /retrieval/inspect
```

Each module ships with: design explanation → code → unit tests (mocked dependencies, following `tests/conftest.py` fixture style) → doc update → example usage snippet, per the stated requirement. Integration tests (real Qdrant/ES/Redis) are added after the corresponding unit tests, consistent with "unit tests before integration tests."

---

## 6. Decisions (Resolved)

1. **Module 0b scope: OpenAI.** User has a working OpenAI API key, so `OpenAIProvider` is the concrete `LLMProvider` implementation built now (small="gpt-4o-mini", large="gpt-4o", per existing `config.py` defaults). `config.py`'s `small_llm_provider`/`large_llm_provider` defaults are updated from `"anthropic"` to `"openai"` so the system is runnable end-to-end with the key actually available. The `LLMProvider` ABC stays provider-agnostic; Anthropic/Gemini/Ollama can be added later as pure additions with no call-site changes.
2. **Semantic cache approach: Qdrant-backed.** Gap 5's resolution is revised: instead of a Redis bounded-scan, `SemanticCacheRepository` (domain ABC, unchanged) is implemented by `QdrantSemanticCacheRepository` — a **new, separate Qdrant collection** (`settings.qdrant_cache_collection_name`, distinct from `document_chunks`) storing `{query_text, answer, citations, model_used, created_at}` as payload with the query embedding as the vector. Lookup is a native top-1 ANN search with a score threshold — no client-side cosine loop, no bounded-scan limitation. This does **not** reuse the existing `VectorRepository` ABC, since that interface is coupled to `DocumentChunk` upsert/search semantics; the cache needs a different payload shape. Redis is still used for the literal hot-path key-value lookup if `cache_key = sha256(query)` matches exactly (already covered by `redis_ttl_query` in `config.py`); Qdrant handles the *semantic* (near-duplicate phrasing) case.
3. **End-of-phase wiring: Yes.** Phase 3 ends with `/chat` (replacing the SSE stub in `chat.py`) and `/retrieval/inspect` (new route) wired to the real `QueryPipeline`. Conversation persistence to Postgres remains deferred (Gap 8 — no `ConversationRepository` yet); the wired `/chat` endpoint generates and streams a real answer but does not yet save it.

Implementation proceeds module by module starting at Module 0a, per the build order in §5.

---

## 7. Module Log

Appended to as each module ships. Each entry: what was built, why it looks the way it does, and how to use it.

### Module 0a — Observability Foundation (done)

**Files:** `src/monitoring/tracing.py`, `src/monitoring/langfuse_tracer.py`, `src/monitoring/stage_tracer.py`. Tests: `tests/unit/monitoring/test_stage_tracer.py` (7 tests, passing).

**What it is:** `traced_stage(name, **attributes)` — one async context manager that opens both an OTel span (`opentelemetry.trace`) and a Langfuse observation (`langfuse.start_as_current_observation`) per pipeline stage, times it, and logs start/end through the existing `structlog` logger. Every Module A–G component wraps its work in this instead of hand-rolling tracing.

**Implementation notes (discovered while building, not assumed upfront):**
- The installed `langfuse` version is **4.9.0**, not the 2.x "classic" API the original gap analysis assumed. Langfuse v4 is OTel-native: `Langfuse().start_as_current_observation(name=..., as_type="span", input=..., output=...)` returns a span-like object with `.update()`, not the old `client.trace()` / `trace.span()` API. The design adapted to this — no code was written against the wrong API.
- Both OTel's `start_as_current_span()` and Langfuse's `start_as_current_observation()` return `opentelemetry.util._decorator._AgnosticContextManager`, which — despite the name — only implements the **synchronous** `with` protocol, not `async with` (confirmed by direct test; raises `TypeError` otherwise). `TracedStage` is therefore a hand-written async context manager whose `__aenter__`/`__aexit__` synchronously drive the underlying sync context managers' `__enter__`/`__exit__`.
- Both the OTel tracer (`get_tracer()`) and the Langfuse client (`get_langfuse_client()`) are safe to call unconditionally with no configuration: `get_tracer()` returns a no-op tracer until `configure_tracing()` is called at app startup, and `Langfuse(public_key=None, secret_key=None)` logs one auth warning and silently no-ops thereafter (confirmed by direct test against the installed package — it does not raise). This means no module needs an `if tracing_enabled` branch anywhere.
- `configure_tracing()` (real OTLP export) and wiring `get_langfuse_client().flush()` into the app shutdown lifecycle are deferred to the Final Integration step (task #10), where `api/main.py` is touched anyway.

**Example usage:**
```python
from src.monitoring.stage_tracer import traced_stage

async def vector_search(query: str, top_k: int):
    async with traced_stage("vector_search", query=query, top_k=top_k) as stage:
        results = await vector_repo.search(query_vector, top_k=top_k)
        stage.set_result(chunks_found=len(results))
        return results
```

**Environment note:** the active venv (`Generative AI/venv`) didn't have this project's pinned dependencies installed. Per user direction, packages are being installed incrementally as each module needs them rather than all at once. Installed so far: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `langfuse`, `structlog`, `prometheus-client`, `pytest`, `pytest-asyncio`, `pytest-mock`.

### Module 0b — LLMProvider Abstraction + OpenAIProvider (done)

**Files:** `src/llm/providers/base.py` (`LLMProvider` ABC), `src/llm/providers/openai_provider.py` (`OpenAIProvider`), `src/llm/registry.py` (`get_llm_provider(settings, role)` factory). `src/config.py`: `small_llm_provider`/`large_llm_provider` defaults changed from `"anthropic"` to `"openai"` since that's the key actually available. Tests: `tests/unit/llm/test_openai_provider.py` (5 tests) + `tests/unit/llm/test_registry.py` (3 tests), all passing.

**What it is:** the interface `LLMMetadataEnricher` (Phase 2) already assumed but that was never built — `complete(prompt, max_tokens, temperature) -> str`, plus a new `stream(...) -> AsyncIterator[str]` for Module G. `get_llm_provider(settings, role="small"|"large")` is the single seam other modules call through; today it only knows how to build `OpenAIProvider`, but adding Anthropic/Gemini/Ollama later is one new branch with zero call-site changes.

**Implementation notes:**
- `stream()` is declared as a plain `def` on the ABC (not `async def`) returning `AsyncIterator[str]`, specifically so callers can do `async for token in provider.stream(...)` with no `await` on the call itself — `OpenAIProvider.stream()` is implemented as an async generator function (`async def` containing `yield`), which is what makes this work.
- Confirmed against the installed `openai==2.32.0` SDK directly (not assumed): `chat.completions.create(model=..., messages=[...], max_tokens=..., temperature=..., stream=True)` still works as the stable chat-completions surface; streamed chunks expose `chunk.choices[0].delta.content`.
- Verified end-to-end that `OpenAIProvider` satisfies `LLMMetadataEnricher`'s pre-existing dependency (`LLMMetadataEnricher(llm_provider=OpenAIProvider(...))` constructs cleanly) — this closes a gap left open since Phase 2, where the enricher had no real provider behind it.
- Full suite (`tests/`) re-run after this module: 35/35 passing, no regressions in Phase 1/2 code.

**Example usage:**
```python
from src.config import get_settings
from src.llm.registry import get_llm_provider

settings = get_settings()
small_llm = get_llm_provider(settings, role="small")

intent_json = await small_llm.complete(
    prompt="Classify the intent of: 'What is the refund policy?'",
    max_tokens=200,
    temperature=0.1,
)

large_llm = get_llm_provider(settings, role="large")
async for token in large_llm.stream(prompt="Answer using the context...", max_tokens=512):
    print(token, end="")
```

### Module A — Query Intelligence Layer (done)

**Files:**
- `src/domain/value_objects/query_intent.py` (`IntentType`, `QueryIntent`)
- `src/domain/value_objects/metadata_filter.py` (`MetadataFilterSpec` + `to_vector_filter()`/`to_bm25_filter()` adapters — resolves Gap 3)
- `src/domain/value_objects/processed_query.py` (`ProcessedQuery`, the layer's output)
- `src/llm/json_parsing.py` (shared `parse_json_response()` used by 3 of the 5 agents)
- `src/retrieval/agents/{intent_classifier,query_rewriter,query_expander,source_selector,filter_generator,query_agent}.py`
- `config.py`: added `query_expansion_count: int = 3`

Tests: `tests/unit/retrieval/test_{intent_classifier,query_rewriter,query_expander,source_selector,filter_generator,query_agent}.py` + `tests/unit/domain/test_{metadata_filter,processed_query}.py` — 27 tests, all passing. Full suite: 62/62.

**What it is:** `QueryAgent.process(query, user_id) -> ProcessedQuery` runs: rewrite → {expand, classify} in parallel → select sources (deterministic) → generate filters, each step wrapped in its own `traced_stage`, with an outer `query_intelligence` span grouping them. Output feeds the Retrieval Layer (Module B) next.

**Design choices worth flagging:**
- **`SourceSelector` is deterministic, not LLM-based.** `IntentClassifier` already produces a `domain` as a side effect of one LLM call; a second LLM round-trip to re-derive essentially the same decision would be redundant cost/latency. It just narrows on `intent.domain` (or returns `[]` for "General" = no restriction). Documented in the class docstring as the seam to extend if the platform ever needs true multi-collection routing — today there's one shared Qdrant collection and one shared ES index, so "source selection" can only mean "narrow the domain filter," not "pick a different index."
- **Every agent fails open, never raises into the caller.** If an LLM call fails or returns unparseable output, each agent logs a warning and returns a safe default (original query unchanged, empty expansion list, `FACTUAL`/`General` intent, minimal filter). A flaky query-intelligence call should degrade retrieval quality, not break the request.
- **`MetadataFilterSpec` exposes `date_from`/`date_to`/`custom` that the adapters silently can't fill into Qdrant/ES today** (Gap 3) — `FilterGenerator` still populates `date_from` from "recency" phrases for transparency/debugging (e.g. visible in a future `/retrieval/inspect` payload), and the adapter logs a warning when dropping it rather than pretending the constraint was applied.

**Pre-existing infra gap found while building this (not yet fixed):** `QdrantVectorRepository._build_filter()` and `ElasticsearchSearchRepository._build_filter_clauses()` (both Phase 2 code) only translate `user_id`, `domain`, `tags` from `VectorSearchFilter`/`BM25SearchFilter` — `file_type` and `document_ids` are defined on those dataclasses but never actually applied to the Qdrant/ES query. `MetadataFilterSpec.to_vector_filter()`/`to_bm25_filter()` pass these fields through correctly, but they'll be silently ignored until Module B patches the two `_build_filter*` methods. Flagged for Module B, since that's where "Metadata Filtering" is the explicit deliverable and where these repositories are actually invoked.

**Example usage:**
```python
from src.retrieval.agents.query_agent import QueryAgent
from src.retrieval.agents.intent_classifier import IntentClassifier
from src.retrieval.agents.query_rewriter import QueryRewriter
from src.retrieval.agents.query_expander import QueryExpander
from src.retrieval.agents.source_selector import SourceSelector
from src.retrieval.agents.filter_generator import FilterGenerator
from src.llm.registry import get_llm_provider
from src.config import get_settings

settings = get_settings()
small_llm = get_llm_provider(settings, role="small")

agent = QueryAgent(
    rewriter=QueryRewriter(small_llm),
    expander=QueryExpander(small_llm),
    classifier=IntentClassifier(small_llm),
    source_selector=SourceSelector(),
    filter_generator=FilterGenerator(small_llm),
    expansion_count=settings.query_expansion_count,
)

processed = await agent.process("what's the refund window for defective items?", user_id=user.id)
# processed.all_queries        -> fan out to vector + BM25 search (Module B)
# processed.filters.to_vector_filter() -> pass to VectorRepository.search()
```

### Module B — Retrieval Layer (done)

**Files:**
- `src/domain/value_objects/retrieval_trace.py` (`RetrievalTrace`)
- `src/retrieval/searchers/{vector_searcher,bm25_searcher}.py`
- `src/retrieval/hybrid_retriever.py` (`HybridRetriever` — runs both concurrently, assembles the trace)

**Multi-tenant isolation fix (found and fixed while building this — see callout above §7):**
- `src/domain/entities/document.py`: `DocumentChunk` gained `user_id`, `domain`, `tags`, `file_type` fields, denormalized from the parent `Document` at ingestion time.
- `src/ingestion/pipeline.py`: `IngestionPipeline.ingest()` now copies those 4 fields from `document`/`document.metadata` onto every chunk right after chunking, before embedding/upsert.
- `src/infrastructure/vector_store/qdrant/repository.py`: `upsert_batch()` now writes `user_id`/`domain`/`tags`/`file_type` into the Qdrant payload (previously omitted entirely, despite `_build_filter()` already building filter conditions against those keys); `_build_filter()` gained `file_type`/`document_ids` conditions; `create_collection_if_not_exists()` gained a `file_type` payload index.
- `src/infrastructure/search/elasticsearch/repository.py`: same shape of fix — `INDEX_MAPPINGS` gained `file_type`, `index_batch()` now writes the 4 fields, `_build_filter_clauses()` gained `file_type`/`document_ids` clauses.
- New tests: `tests/unit/infrastructure/test_qdrant_repository.py` (7 tests), `tests/unit/infrastructure/test_elasticsearch_repository.py` (6 tests) — directly exercise `_build_filter`/`_build_filter_clauses` and assert the payload/document body actually contains the denormalized fields.

**What Module B itself is:** `VectorSearcher.search(queries, top_k, filters)` and `BM25Searcher.search(queries, top_k, filters)` each implement query fan-out — `ProcessedQuery.all_queries` (rewritten + expansions) are all searched against their backend, and results are merged by keeping the **highest score per chunk_id** across variants, then truncated to `top_k` and re-ranked 1..n. This is the "Input: rewritten + expanded Qs" pattern from `04_data_flow.md`'s query data flow diagram. `HybridRetriever.retrieve(...)` runs both searchers concurrently via `asyncio.gather`, times each branch independently (can't just sum durations — they overlap), and returns `(vector_results, bm25_results, RetrievalTrace)`. Fusion (RRF) is deliberately **not** in this class — that's Module C.

Tests: `tests/unit/retrieval/test_{vector_searcher,bm25_searcher,hybrid_retriever}.py` — 11 tests. Full suite: 86/86 passing.

**Example usage:**
```python
from src.retrieval.searchers.vector_searcher import VectorSearcher
from src.retrieval.searchers.bm25_searcher import BM25Searcher
from src.retrieval.hybrid_retriever import HybridRetriever

retriever = HybridRetriever(
    vector_searcher=VectorSearcher(vector_repo=qdrant_repo, embedding_provider=embedder),
    bm25_searcher=BM25Searcher(search_repo=es_repo),
)

vector_results, bm25_results, trace = await retriever.retrieve(
    queries=processed_query.all_queries,
    vector_top_k=settings.vector_search_top_k,
    bm25_top_k=settings.bm25_search_top_k,
    vector_filter=processed_query.filters.to_vector_filter(),
    bm25_filter=processed_query.filters.to_bm25_filter(),
)
# vector_results, bm25_results -> Module C (RRF fusion)
# trace -> accumulates through C/D/E, eventually returned by /retrieval/inspect
```

### Module C — Fusion Layer (done)

**Files:**
- `src/domain/value_objects/retrieval_candidate.py` (`FusedChunk`, `RerankedChunk`)
- `src/retrieval/fusers/{rrf_fuser,score_normalizer,duplicate_remover,fuser}.py`

**What it is:** `Fuser.fuse(vector_results, bm25_results) -> list[FusedChunk]` runs three pure, I/O-free, fully-sync steps — `RRFFusion.fuse()` (combine by reciprocal rank, keyed by `chunk_id`), `ScoreNormalizer.normalize()` (min-max scale raw scores to `[0,1]` for display), `DuplicateRemover.remove()` (collapse exact-duplicate content via `DocumentChunk.content_hash`) — wrapped in one `traced_stage("fusion", ...)` span. Only the composing `Fuser.fuse()` is `async def`; the three steps it calls are plain sync methods, since none of them do I/O — that's also why they have no internal tracing of their own, consistent with the "one span per retrieval stage" granularity established in Module B (vector_search/bm25_search), not "one span per helper function."

**Design choices worth flagging:**
- **RRF reads `.rank`, never the raw `.score`/`.bm25_score`.** Vector cosine similarity and BM25 scores are on incomparable scales; blending them by value would need arbitrary weighting. Reciprocal-rank fusion only needs each list's relative ordering, which `VectorSearcher`/`BM25Searcher` already assign.
- **`ScoreNormalizer` is purely cosmetic** — it never affects ranking (RRF has already produced final order by the time normalization runs); it exists only so a future `/retrieval/inspect` payload can show comparable 0-1 scores alongside the raw ones, matching the example response shape in `07_api_design.md`.
- **Dedup is exact-content-hash only**, not embedding-similarity near-dup detection — documented as a deliberate scope cut (an extra embedding-comparison pass for marginal benefit at this stage), not an oversight.

Tests: `tests/unit/retrieval/test_{rrf_fuser,score_normalizer,duplicate_remover,fuser}.py` — 16 tests, all pure/no mocking needed except the composing `Fuser`. Full suite: 102/102 passing.

**Example usage:**
```python
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.fusers.rrf_fuser import RRFFusion
from src.retrieval.fusers.score_normalizer import ScoreNormalizer
from src.retrieval.fusers.duplicate_remover import DuplicateRemover

fuser = Fuser(
    rrf=RRFFusion(k=settings.rrf_k),
    normalizer=ScoreNormalizer(),
    deduplicator=DuplicateRemover(),
)

fused_chunks = await fuser.fuse(vector_results, bm25_results)
# fused_chunks -> Module D (reranking)
```

### Module D — Reranking Layer (done)

**Files:** `src/retrieval/rerankers/{base,bge_reranker,cohere_reranker,registry}.py`.

**What it is:** `Reranker` ABC with `async rerank(query, candidates: list[FusedChunk], top_n) -> list[RerankedChunk]`. `BGEReranker` wraps a `sentence_transformers.CrossEncoder` (local, no API cost, needs the model loaded in-process); `CohereReranker` wraps `cohere.AsyncClientV2.rerank()` (hosted API, no local model). `get_reranker(settings)` picks one via `settings.reranker_provider`.

**Implementation notes:**
- **Dependency injection over construction, for both rerankers.** `BGEReranker`/`CohereReranker` take an already-constructed `cross_encoder`/`client` in their constructor rather than building one from a model name/API key internally — only `get_reranker()` (the registry/factory) actually instantiates `CrossEncoder(model_name)` or `cohere.AsyncClientV2(...)`. This matters because `CrossEncoder(...)` loads (and may download) a real multi-GB model from HuggingFace Hub — unit tests inject a fake object with the same `.predict()` shape and never touch the registry's construction path. The registry itself is tested by patching `CrossEncoder`/`cohere.AsyncClientV2` at the module level.
- **`CrossEncoder.predict()` is sync and CPU/GPU-bound** (not I/O), so `BGEReranker` runs it via `asyncio.to_thread()` to avoid blocking the event loop — consistent with the project's async-first principle.
- **BGE and Cohere have inverted responsibilities.** Cohere's rerank API already returns results sorted by relevance and truncated to `top_n` — `CohereReranker` just maps `result.index` back to the original `FusedChunk` and assigns `final_rank` from response order. `CrossEncoder.predict()` returns raw, unsorted scores for every pair — `BGEReranker` does the sort + truncate + rank-assignment itself.
- Verified against the actually-installed SDKs (not assumed): `cohere==7.0.4`'s `AsyncClientV2.rerank(model=, query=, documents=, top_n=)` and its `V2RerankResponse.results[].{index, relevance_score}` shape; `sentence-transformers==5.6.0`'s `CrossEncoder.predict(list[(query, doc)])`.
- `torch`/`transformers`/`huggingface_hub` were already present in the venv (shared across other projects), so installing `sentence-transformers` + `cohere` for this module was fast — no multi-GB download was needed for the unit tests themselves (no real model is loaded; tests inject fakes).

Tests: `tests/unit/retrieval/test_{bge_reranker,cohere_reranker,reranker_registry}.py` — 13 tests. Full suite: 115/115 passing.

**Example usage:**
```python
from src.retrieval.rerankers.registry import get_reranker

reranker = get_reranker(settings)  # constructed once at startup/DI wiring

reranked_chunks = await reranker.rerank(
    query=processed_query.rewritten_query,
    candidates=fused_chunks,
    top_n=settings.rerank_top_n,
)
# reranked_chunks -> Module E (context processing)
```

### Module E — Context Processing Layer (done)

**Files:**
- `src/domain/value_objects/context_bundle.py` (`CompressedChunk`)
- `src/retrieval/context/{token_counter,token_budget_manager,context_deduplicator,context_compressor,citation_preserver,context_processor}.py`
- `config.py`: added `context_max_tokens: int = 6000`

**Another round-trip gap found and fixed first (same root cause as Module B):** citations need `chunk.document_name`/`chunk.chunk_metadata.page_number`/`.section` on the chunk objects that come back *from a search call*, not just at ingest time. Auditing this surfaced that `QdrantVectorRepository.search()` and `ElasticsearchSearchRepository.search()` only ever reconstructed `id/document_id/content/position/chunk_type/token_count` from the stored payload/document — `page_number`, `section`, `contains_table`, `parent_chunk_id`, and (after the Module B fix) `user_id`/`domain`/`tags`/`file_type` were all being written correctly but silently dropped on the way back out. Added `document_name` to both write paths and the ES mapping, and fixed both `search()` methods to fully reconstruct every stored field. New tests: `test_search_reconstructs_denormalized_fields_from_payload` (Qdrant) and `_from_source` (ES).

**What Module E itself is:** `ContextProcessor.process(query, chunks) -> (list[CompressedChunk], dict[UUID, Citation])` runs, as one traced stage: `ContextDeduplicator.deduplicate()` (containment-based, on original content) → `ContextCompressor.compress()` (small LLM extracts only the query-relevant sentences per chunk, own nested span since it's LLM-calling) → `TokenBudgetManager.select()` (greedy, stops at the first chunk that would overflow `context_max_tokens`) → `CitationPreserver.build()` (assigns final `[1]..[n]` indices and builds `Citation` objects, reusing the `Citation` entity already defined in `domain/entities/conversation.py` rather than inventing a parallel one).

**Design choices worth flagging:**
- **`ContextDeduplicator` (Module E) is not redundant with `DuplicateRemover` (Module C).** C catches *exact* content-hash duplicates across the large pre-rerank candidate pool (e.g. the same boilerplate chunk indexed from two documents). E catches *containment* — a smaller chunk whose entire content is a substring of an already-kept, higher-ranked chunk (e.g. a child chunk wholly inside its parent's text, since `ChunkType.PARENT` chunks do get indexed into Elasticsearch's BM25 index via `index_batch(saved_chunks)` even though they're excluded from Qdrant — both can legitimately surface together). It's one-directional by design — a larger chunk arriving after a smaller one is kept, since it adds real new surrounding context; only documented as a partial solution, not full interval-merging.
- **Ordering is deliberate: dedup before compression.** An LLM-compressed excerpt may rephrase text, breaking substring containment checks that worked on the original wording — so containment dedup has to run on raw content, before compression touches it.
- **`ContextCompressor` fails open and runs all chunks concurrently.** A failed compression call keeps the original (uncompressed) content rather than dropping the chunk; an LLM response of exactly `"NONE"` is the only thing that drops a chunk. All chunks compress via `asyncio.gather`, since each is an independent small-LLM call on an already-bounded (`rerank_top_n`) set.
- **`TokenCounter` falls back to `cl100k_base`** when `tiktoken.encoding_for_model()` doesn't recognize the configured model name — tiktoken's registry lags behind new model releases, and an unrecognized name shouldn't break budgeting.

Tests: `tests/unit/retrieval/test_{token_counter,token_budget_manager,context_deduplicator,context_compressor,citation_preserver,context_processor}.py` — 22 tests. Plus the repository round-trip tests above. Full suite: 139/139 passing.

**Example usage:**
```python
from src.retrieval.context.context_processor import ContextProcessor
from src.retrieval.context.context_deduplicator import ContextDeduplicator
from src.retrieval.context.context_compressor import ContextCompressor
from src.retrieval.context.token_budget_manager import TokenBudgetManager
from src.retrieval.context.token_counter import TokenCounter
from src.retrieval.context.citation_preserver import CitationPreserver

processor = ContextProcessor(
    deduplicator=ContextDeduplicator(),
    compressor=ContextCompressor(llm_provider=small_llm),
    budget_manager=TokenBudgetManager(
        token_counter=TokenCounter(model=settings.openai_large_model),
        max_tokens=settings.context_max_tokens,
    ),
    citation_preserver=CitationPreserver(),
)

compressed_chunks, citations = await processor.process(
    query=processed_query.rewritten_query, chunks=reranked_chunks
)
# compressed_chunks, citations -> Module G (answer pipeline)
```

### Module F — Semantic Cache Layer (done)

**Files:**
- `src/domain/value_objects/cache_entry.py` (`SemanticCacheEntry`)
- `src/domain/repositories/cache_repository.py` (`SemanticCacheRepository` ABC)
- `src/infrastructure/vector_store/qdrant/cache_repository.py` (`QdrantSemanticCacheRepository`)
- `src/retrieval/cache/semantic_cache.py` (`SemanticCache` service)
- `config.py`: added `qdrant_cache_collection_name: str = "semantic_query_cache"`, `semantic_cache_score_threshold: float = 0.95`

**A real production-breaking bug found and fixed first, unrelated to caching:** while writing the cache repository against the actually-installed `qdrant-client` (1.16.2), `AsyncQdrantClient.search()` turned out not to exist anymore — `AttributeError: type object 'AsyncQdrantClient' has no attribute 'search'`, confirmed directly against the installed package, not assumed. It was replaced by `query_points()`, which returns a `QueryResponse` wrapping a `.points` list rather than a bare list. **This means `QdrantVectorRepository.search()` (Phase 2 code, already relied on by Modules B/C/D/E) has been silently broken since whenever this project's qdrant-client was last upgraded** — and none of the unit tests written across Modules B/C/D/E caught it, because every one of them mocked the client with a bare `AsyncMock()`, which happily fabricates a `.search` attribute that doesn't exist on the real class. Fixed `QdrantVectorRepository.search()` to call `query_points()` and iterate `.points`. **Also hardened the test fixture**: `tests/unit/infrastructure/test_qdrant_repository.py`'s `mock_client` fixture now uses `AsyncMock(spec=AsyncQdrantClient)`, so calling a method that doesn't exist on the real client raises `AttributeError` in the test itself instead of silently succeeding — this is what should have caught the bug originally, and will catch the next API drift instead of requiring it to be found by hand again.

**What Module F itself is:** `SemanticCache.lookup(query)` embeds the query (via the same `EmbeddingProvider` used for document chunks — same model, comparable vector space) and asks `SemanticCacheRepository.find_similar()` for a near-duplicate previously-answered query above `score_threshold`. `SemanticCache.store(...)` embeds and persists a new `{query_text, answer, citations, model_used}` entry. `QdrantSemanticCacheRepository` implements the repository against a **separate, dedicated Qdrant collection** (`semantic_query_cache`, distinct from `document_chunks`) — per the resolved design decision in §6, this gives native ANN similarity search instead of the originally-floated Redis bounded-scan approach, at the cost of the cache not being literally Redis-resident.

**Design choices worth flagging:**
- **Entries are keyed by a fresh UUID, not by query text.** Lookup is purely by vector similarity against `score_threshold` — this is the entire point of a *semantic* cache: "What's the refund window?" and "How long do I have to return something?" should hit the same cached answer despite sharing no text.
- **`SemanticCacheRepository` doesn't reuse the existing `VectorRepository` ABC**, even though both end up Qdrant-backed. `VectorRepository` is coupled to `DocumentChunk` upsert/search semantics (`upsert_batch(chunks: list[DocumentChunk])`); the cache's payload shape (`query_text`/`answer`/`citations`/`model_used`) is a different concept entirely. Keeping them separate means swapping either implementation later doesn't ripple into the other.

Tests: `tests/unit/infrastructure/test_qdrant_cache_repository.py` (6 tests) + `tests/unit/retrieval/test_semantic_cache.py` (4 tests). Full suite: 149/149 passing.

**Example usage:**
```python
from src.retrieval.cache.semantic_cache import SemanticCache
from src.infrastructure.vector_store.qdrant.cache_repository import QdrantSemanticCacheRepository

cache = SemanticCache(
    repository=QdrantSemanticCacheRepository(client=qdrant_client, collection_name=settings.qdrant_cache_collection_name),
    embedding_provider=embedder,
    score_threshold=settings.semantic_cache_score_threshold,
)

cached = await cache.lookup(user_query)
if cached is not None:
    return cached.answer, cached.citations  # skip Modules A-G entirely

# ... run the full pipeline ...
await cache.store(user_query, answer, citations, model_used=large_llm.model_id)
```

### Module G — Answer Pipeline (done)

**Files:**
- `src/domain/value_objects/context_bundle.py`: added `AssembledContext` (alongside Module E's `CompressedChunk`)
- `src/retrieval/answer/{context_assembler,prompt_builder,stream_generator,citation_validator,answer_pipeline}.py`
- `src/retrieval/{context,cache,answer}/__init__.py`: added — these three packages (created across Modules E/F/G) were missing explicit `__init__.py` files; tests still passed via Python's implicit namespace packages, but this was inconsistent with every other package in the codebase, which uses explicit `__init__.py` throughout
- `config.py`: added `answer_max_tokens: int = 1024`, `answer_temperature: float = 0.3`

**Note on session continuity:** this module was split across two sessions (a transcript error interrupted the first one after `ContextAssembler`/`PromptBuilder`/`StreamGenerator` were written but before `CitationValidator`/`AnswerPipeline` existed). Before resuming, the repo was audited file-by-file against `src/retrieval/answer/`, `tests/`, `config.py`, and this doc to confirm exactly what existed vs. what was assumed — the three already-written files were verified intact (not truncated) and the full suite still passed (149/149) before continuing. No code was lost; this entry covers the full module as the audit + completion turned out.

**What it is:** `AnswerPipeline.generate(query, chunks, citations, max_tokens, temperature)` is itself an **async generator** yielding SSE-ready event dicts — `{"type": "token", "content": ...}` while streaming, then one `{"type": "done", "answer": ..., "citations": [...]}`, or `{"type": "error", "code": ..., "message": ...}` if generation fails mid-stream. This is not an arbitrary shape — it matches the `/chat` SSE event contract already documented in `07_api_design.md` and already stubbed in `chat.py` exactly, so the Final Integration step just needs to `json.dumps()` and forward each yielded dict.

Internally: `ContextAssembler.assemble()` formats the Module E output into one string with inline `[n]` markers (reusing the indices `CitationPreserver` already assigned) → `PromptBuilder.build()` wraps it with a system instruction (answer only from context, cite with `[n]`, say so if the context is insufficient) → `StreamGenerator.generate()` wraps `LLMProvider.stream()` in its own `traced_stage("answer_generation", ...)` spanning the whole token stream → `CitationValidator.validate()` regex-extracts which `[n]` markers actually appear in the accumulated answer text and keeps only the ones that correspond to a real citation, dropping anything the LLM hallucinated.

**Design choices worth flagging:**
- **The orchestrator is an async generator, not a function returning a result object.** Token-by-token SSE streaming and "give me the final validated citations" are in tension — a normal `async def` can't both stream live and return a value after. Yielding discriminated event dicts resolves this without inventing a parallel non-streaming code path: the caller accumulates `tokens.append()` themselves while forwarding each `token` event, then receives the `done` event once `CitationValidator` has run on the full text.
- **`latency_ms` is deliberately not in the `done` event.** `AnswerPipeline` only knows about its own stage's timing — total request latency spans Modules A–G, which only the eventual `QueryPipeline`/route handler can measure. Module G doesn't fabricate a number it doesn't actually have.
- **`StreamGenerator`'s span wraps the whole stream, not each token.** `async with traced_stage(...)` opens before the first token and its `__aexit__` only runs once the generator is exhausted — valid because the `async with` block contains the `yield`, and Python runs the context manager's exit logic when generator iteration completes (including via `GeneratorExit` if the caller stops early).
- **`CitationValidator` purely deals with what the LLM actually wrote**, not what was offered — a citation present in `citations` but never referenced in the answer text is silently dropped from the final list (it simply wasn't used), and a referenced index with no matching citation is logged and dropped (hallucinated source).

Tests: `tests/unit/retrieval/test_{context_assembler,prompt_builder,stream_generator,citation_validator,answer_pipeline}.py` — 19 tests. Full suite: 168/168 passing.

**Example usage:**
```python
from src.retrieval.answer.answer_pipeline import AnswerPipeline
from src.retrieval.answer.context_assembler import ContextAssembler
from src.retrieval.answer.prompt_builder import PromptBuilder
from src.retrieval.answer.stream_generator import StreamGenerator
from src.retrieval.answer.citation_validator import CitationValidator

pipeline = AnswerPipeline(
    context_assembler=ContextAssembler(),
    prompt_builder=PromptBuilder(),
    stream_generator=StreamGenerator(llm_provider=large_llm),
    citation_validator=CitationValidator(),
)

async for event in pipeline.generate(
    query=processed_query.rewritten_query,
    chunks=compressed_chunks,
    citations=citations,
    max_tokens=settings.answer_max_tokens,
    temperature=settings.answer_temperature,
):
    yield f"data: {json.dumps(event)}\n\n"  # this is exactly chat.py's job in Final Integration
```

### Final Integration — QueryPipeline + wiring (done)

**Files:**
- `src/retrieval/pipeline.py` (`QueryPipeline`, `RetrievalInspection`)
- `src/application/use_cases/{process_query,inspect_retrieval}.py`
- `src/api/dependencies.py` (`get_query_pipeline()` singleton factory wiring all of Modules A–G)
- `src/api/routes/chat.py`: rewritten — real pipeline replaces the Phase 2 SSE stub
- `src/api/routes/retrieval.py`: new — `POST /retrieval/inspect`
- `src/api/main.py`: registers the new `retrieval` router
- `src/retrieval/answer/stream_generator.py`: added `model_id` property; `answer_pipeline.py`: `done` event gained `model_used`, needed so `QueryPipeline` knows what to record in the semantic cache
- `05_folder_structure.md`, `10_build_roadmap.md`: updated to match what was actually built (Phase 3's scope absorbed most of the original Phase 4; Phase 4 is now narrowed to multi-provider LLMs + conversation persistence + auth-derived `user_id`)

**Note on session continuity:** this task started, was interrupted by a transcript error partway through Module G, and resumed after an explicit audit (see the "Note on session continuity" under Module G) confirmed exactly what existed before continuing — no work was lost or duplicated.

**What it is:** `QueryPipeline` has two entry points sharing one private `_retrieve()` core:
- **`inspect(query, user_id) -> RetrievalInspection`** runs Modules A→B→C→D only (query intelligence, retrieval, fusion, reranking) and returns every intermediate result plus a fully-populated `RetrievalTrace`. No LLM answer is generated — this matches the documented `/retrieval/inspect` contract in `07_api_design.md` exactly, which never includes a generated answer.
- **`answer(query, user_id) -> AsyncIterator[dict]`** checks `SemanticCache.lookup()` first (short-circuits A–G entirely on a hit); on a miss, runs the same A→D core, then Module E (`ContextProcessor`) and Module G (`AnswerPipeline`), forwarding G's SSE event dicts directly and storing the result in the semantic cache after the `done` event.

`_retrieve()` is where `RetrievalTrace` actually gets fully populated: `HybridRetriever.retrieve()` (Module B) already fills in `vector_search_ms`/`bm25_search_ms`/`vector_count`/`bm25_count`/`queries_used`; `_retrieve()` measures and fills in `query_processing_ms` (around `QueryAgent.process()`), `fusion_ms`/`fused_count` (around `Fuser.fuse()`), and `reranking_ms`/`reranked_count`/`total_ms` (around `Reranker.rerank()` and overall) — exactly the design called for back in Module B's doc entry ("populated incrementally across Modules A-E... by whichever orchestrator measures the actual end-to-end wall-clock time").

**Design choices worth flagging:**
- **DI is a manual singleton factory, not the `dependency-injector` package.** `pyproject.toml` pins `dependency-injector>=4.41.0`, but nothing in this codebase — Phase 1, 2, or 3 — actually uses it; `IngestionPipeline` is already manually constructed wherever it's needed. `get_query_pipeline()` in `src/api/dependencies.py` follows the same manual-construction style, cached via the same module-level-singleton pattern already used by `get_settings()`/`get_redis_client()`/`get_langfuse_client()` (a `global` variable + `is None` check, not `functools.lru_cache`, for consistency). Introducing a real DI container for the first time would be a bigger architectural change than this PR's scope warrants — flagged here as a reasonable Phase 7/8 cleanup, not done now.
- **`request.user_id` on `/chat` and `/retrieval/inspect` is a stand-in, not real auth.** Neither endpoint has authentication wired yet (Phase 7). Since `MetadataFilterSpec.user_id` is what powers the multi-tenant Qdrant/ES filter fixed in Module B, leaving it un-threadable would mean tenant isolation could never actually engage through these endpoints. The field is documented inline as temporary, to be replaced by JWT-derived identity once Phase 7 lands.
- **`/chat`'s `filters` and `debug` request fields are accepted but not honored**, logged with a warning when non-empty rather than silently ignored or rejected. `filters` would need merge semantics against Module A's auto-generated `MetadataFilterSpec` (client override vs. LLM-inferred) — a real design decision, not a wiring task, so it's left for whoever picks this up next rather than guessed at here.
- **Conversation persistence is still not wired** (Gap 8, unchanged) — `/chat` generates and streams a real answer but does not save `Message`/`Conversation` to Postgres, since `ConversationRepository` doesn't exist yet.

Tests: `tests/unit/retrieval/test_query_pipeline.py` (6 tests) + `tests/unit/application/test_{process_query,inspect_retrieval}.py` (4 tests) + `tests/unit/api/test_{dependencies,main}.py` (6 tests, including a real (mocked-reranker) construction of the full dependency graph). **Full suite: 184/184 passing.**

**Example usage:**
```python
# What a client now gets for real:
# POST /api/v1/chat {"query": "what is the refund policy?", "stream": true}
#   -> SSE: data: {"type": "token", "content": "The"}
#           data: {"type": "token", "content": " refund"}
#           ...
#           data: {"type": "done", "answer": "...", "citations": [...], "model_used": "gpt-4o", "latency_ms": 1450}
#           data: [DONE]

# POST /api/v1/retrieval/inspect {"query": "what is the refund policy?"}
#   -> 200 JSON: {"original_query", "processed_query", "vector_results",
#                 "bm25_results", "fused_results", "reranked_results", "latency_breakdown"}
```

---

## Phase 3: Complete

All 10 build-order items (§5) are done: Module 0a/0b, A–G, and Final Integration. 184 unit tests across every module, all passing. Two real production bugs found and fixed along the way (multi-tenant isolation silently broken; `AsyncQdrantClient.search()` removed in the installed client version), both with regression tests and a hardened mock (`spec=AsyncQdrantClient`) to catch the next API drift automatically. Remaining work — integration tests against real infra, multi-provider LLMs, conversation persistence, real auth — is captured as Phase 4 in `10_build_roadmap.md`.
