# Risk Analysis — Enterprise Agentic RAG Platform

## Risk Matrix

Severity: **C** = Critical | **H** = High | **M** = Medium | **L** = Low
Likelihood: **P** = Probable | **O** = Occasional | **R** = Rare

---

## 1. Technical Risks

### R-T01: LLM Hallucination in Answers
**Severity:** C | **Likelihood:** P | **Score:** Critical

**Description:** Large LLM generates factually incorrect answers despite retrieved context. Especially dangerous for policy, legal, or financial documents.

**Mitigations:**
- Citation requirement: every claim must reference a source chunk
- Context compression step filters low-relevance chunks before generation
- System prompt includes explicit "only answer from provided context" instruction
- RAGAS faithfulness metric runs on every evaluation cycle
- Hallucination score tracked via DeepEval
- User feedback (thumbs down + "hallucinated" tag) triggers alert

**Residual risk:** Moderate — LLMs can still construct plausible but unsupported answers.

---

### R-T02: Retrieval Miss — Relevant Documents Not Returned
**Severity:** H | **Likelihood:** O | **Score:** High

**Description:** Hybrid search fails to retrieve the correct chunks, leading to incomplete or wrong answers even though the information exists in the corpus.

**Mitigations:**
- Query expansion (3-5 variants) reduces single-query miss probability
- Hybrid retrieval (vector + BM25) covers semantic and lexical gaps
- Parent-child chunking: child chunks for precision, parents for context
- RAGAS context recall metric monitors retrieval quality
- Retrieval Inspector UI allows developers to debug misses directly

**Residual risk:** Low-Moderate — some edge-case queries may still miss.

---

### R-T03: Embedding Model Drift / Incompatibility
**Severity:** H | **Likelihood:** R | **Score:** Medium

**Description:** Switching embedding models mid-deployment leaves mismatched vectors (old model) alongside new vectors, causing degraded retrieval.

**Mitigations:**
- Each chunk stores `embedding_model` field in both Qdrant payload and PostgreSQL
- Model change triggers full re-indexing job (async, tracked)
- Blue/green collection strategy in Qdrant during re-index
- Version-pinned embedding model IDs in system settings

**Residual risk:** Low — re-indexing process is defined and automated.

---

### R-T04: Qdrant / Elasticsearch Downtime
**Severity:** H | **Likelihood:** O | **Score:** High

**Description:** Vector or BM25 search becomes unavailable, blocking all chat functionality.

**Mitigations:**
- Health checks on `/health` endpoint expose dependency status immediately
- Fallback: if Qdrant down, attempt BM25-only retrieval (lower quality but functional)
- Qdrant snapshot schedule to S3 (hourly)
- Elasticsearch replica shards for high availability
- Circuit breaker pattern (tenacity) wraps all external calls

**Residual risk:** Low — degraded mode keeps system partially operational.

---

### R-T05: Context Window Overflow
**Severity:** M | **Likelihood:** O | **Score:** Medium

**Description:** Large number of retrieved chunks + conversation history exceeds LLM context window, causing truncation or API errors.

**Mitigations:**
- Context compression step reduces token count before generation
- Hard token budget enforced before calling large LLM (max 80% of context window)
- Conversation history truncated to last N turns when budget is tight
- `token_count` tracked per chunk — only include chunks that fit

**Residual risk:** Low — budget management is enforced at code level.

---

### R-T06: Prompt Injection via Malicious Documents
**Severity:** H | **Likelihood:** O | **Score:** High

**Description:** User uploads document containing crafted text (e.g., "Ignore instructions and...") that manipulates LLM behavior during answer generation.

**Mitigations:**
- System prompt always placed before context with clear delimiters
- Input sanitization on document text during ingestion
- Jailbreak detection layer (future: using LLM-as-classifier)
- Monitoring for anomalous response patterns

**Residual risk:** Moderate — fully preventing prompt injection is an unsolved problem.

---

## 2. Operational Risks

### R-O01: LLM API Cost Overrun
**Severity:** H | **Likelihood:** P | **Score:** High

**Description:** Uncontrolled LLM API usage (tokens) leads to unexpected billing costs.

**Mitigations:**
- Per-user rate limiting (30 queries/min)
- Token usage tracked per request and stored in PostgreSQL
- Prometheus alert: `rag_llm_tokens_total > threshold`
- Langfuse cost tracking per trace
- Monthly budget cap configurable per deployment
- Query + embedding response caching reduces repeat API calls by ~40%

**Residual risk:** Low-Moderate with proper budget alerts.

---

### R-O02: Long Ingestion Times Blocking Users
**Severity:** M | **Likelihood:** P | **Score:** Medium

**Description:** Large PDF (100MB, 500 pages) ingestion takes 10+ minutes, leaving user waiting with no feedback.

**Mitigations:**
- Async ingestion — immediate 202 Accepted response
- Real-time status tracked in Redis (`doc_status:{id}`)
- Streamlit UI polls status and shows progress bar
- Ingestion broken into steps — partial progress visible (loaded, extracted, chunked, indexed)

**Residual risk:** Low — async pattern handles this by design.

---

### R-O03: Database Schema Migration Failure
**Severity:** H | **Likelihood:** R | **Score:** Medium

**Description:** Alembic migration fails in production, leaving schema in inconsistent state.

**Mitigations:**
- All migrations are reversible (downgrade scripts required)
- Staging environment runs migrations first before production
- Pre-migration PostgreSQL backup automated in CI/CD
- Migration runs in transaction — auto-rollback on failure

**Residual risk:** Low.

---

### R-O04: Redis Eviction Invalidating Sessions
**Severity:** M | **Likelihood:** O | **Score:** Medium

**Description:** Under memory pressure Redis evicts session tokens, logging out active users.

**Mitigations:**
- Redis `maxmemory-policy` set to `volatile-lru` — only evict TTL-tagged keys
- Session tokens use `volatile` key type (have TTL)
- JWT is stateless backup — client holds token, can re-verify without Redis for read operations
- Redis memory monitored via Prometheus

**Residual risk:** Low.

---

## 3. Security Risks

### R-S01: API Key Leak
**Severity:** C | **Likelihood:** O | **Score:** Critical

**Description:** API key leaked (logs, code, client-side) gives unauthorized access to all documents.

**Mitigations:**
- Only `key_hash` stored in database (bcrypt or sha256 with salt)
- Raw key returned only once at creation time
- API keys can be immediately revoked
- Multi-tenant: each user's documents are scoped to their `user_id`
- Audit log for all API key usage
- Log scrubbing: API keys never logged (structured logging field exclusion)

**Residual risk:** Low — industry-standard key management.

---

### R-S02: Unauthorized Cross-Tenant Data Access
**Severity:** C | **Likelihood:** R | **Score:** Critical

**Description:** User A accesses User B's documents or conversations.

**Mitigations:**
- All database queries include `WHERE user_id = :current_user_id`
- Repository layer enforces tenant scoping at data access level
- Integration tests verify tenant isolation
- Qdrant queries include `must: [{key: "user_id", match: {value: ...}}]`

**Residual risk:** Very Low — enforced at multiple layers.

---

### R-S03: File Upload Abuse (Malware, DoS)
**Severity:** H | **Likelihood:** O | **Score:** High

**Description:** User uploads malicious file (executable disguised as PDF) or extremely large file causing DoS.

**Mitigations:**
- File type validation: MIME type checking, not just extension
- Max file size: 100MB hard limit (enforced at FastAPI layer)
- Virus scanning integration (ClamAV — future)
- Files processed in isolated temp directory, deleted after ingestion
- Rate limit: 10 uploads/min per user

**Residual risk:** Low-Moderate — MIME validation catches most cases.

---

## 4. Scalability Risks

### R-SC01: Single Streamlit Instance Bottleneck
**Severity:** M | **Likelihood:** P | **Score:** Medium

**Description:** Streamlit is stateful and does not scale horizontally — a single busy instance becomes a bottleneck.

**Mitigations:**
- Streamlit is stateless in our design (all state in FastAPI/Redis)
- Session state stored server-side in Redis, not Streamlit session state
- FastAPI handles all heavy computation — Streamlit is just a thin client
- Future: move to custom React frontend if traffic demands it

**Residual risk:** Moderate for high-traffic deployments.

---

### R-SC02: Elasticsearch BM25 Index Growth
**Severity:** M | **Likelihood:** O | **Score:** Medium

**Description:** BM25 index grows unbounded as documents are added, degrading search performance and increasing storage.

**Mitigations:**
- Index sharding: 3 shards from day one, expandable
- Delete documents trigger ES index deletion
- Index lifecycle management (ILM) policy for archiving old data
- Qdrant also stores content for fallback if ES becomes slow

**Residual risk:** Low with proper ILM configuration.

---

## 5. Risk Summary Table

| ID | Risk | Severity | Likelihood | Priority |
|----|------|----------|------------|---------|
| R-T01 | LLM Hallucination | C | P | 1 |
| R-S01 | API Key Leak | C | O | 2 |
| R-S02 | Cross-Tenant Access | C | R | 3 |
| R-T04 | Vector DB Downtime | H | O | 4 |
| R-T06 | Prompt Injection | H | O | 5 |
| R-O01 | Cost Overrun | H | P | 6 |
| R-S03 | Malicious Upload | H | O | 7 |
| R-T02 | Retrieval Miss | H | O | 8 |
| R-T03 | Embedding Drift | H | R | 9 |
| R-O03 | Migration Failure | H | R | 10 |
| R-T05 | Context Overflow | M | O | 11 |
| R-O02 | Long Ingestion | M | P | 12 |
| R-O04 | Redis Eviction | M | O | 13 |
| R-SC01 | Streamlit Bottleneck | M | P | 14 |
| R-SC02 | ES Index Growth | M | O | 15 |

---

## 6. Architecture Weaknesses & Recommended Improvements

### Weakness 1: Synchronous Query Pipeline
**Problem:** The query pipeline is currently fully synchronous — each step waits for the previous. This increases p95 latency.

**Recommendation:** Parallelize vector search + BM25 search using `asyncio.gather`. Both can run concurrently since they hit different systems.

```python
vector_results, bm25_results = await asyncio.gather(
    vector_searcher.search(queries),
    bm25_searcher.search(queries)
)
```

**Impact:** ~40% latency reduction on retrieval step.

---

### Weakness 2: No Streaming for Ingestion Status
**Problem:** Polling for ingestion status adds unnecessary API calls.

**Recommendation:** Add WebSocket or SSE endpoint for real-time ingestion progress:
`GET /api/v1/documents/{id}/stream-status`

---

### Weakness 3: RAGAS Evaluation Requires LLM API Calls
**Problem:** RAGAS uses LLMs to score faithfulness and relevancy — expensive at scale.

**Recommendation:** 
- Run RAGAS on sampled subset (10-20% of queries) for continuous monitoring
- Schedule full eval weekly, not after every query

---

### Weakness 4: No Chunking Strategy for Very Large Tables
**Problem:** A table spanning many pages will either be cut arbitrarily or create an enormous single chunk.

**Recommendation:** Implement table-aware chunking: split multi-page tables by row groups (every N rows) and include column headers in each split.

---

### Weakness 5: Single LLM Call for Metadata Enrichment
**Problem:** LLM metadata enrichment (summary + tags + domain + entities) in a single call may produce lower quality for complex documents.

**Recommendation:** Split into two calls — (1) summary + domain classification, (2) entity extraction — which allows better-targeted prompts and independent failure handling.
