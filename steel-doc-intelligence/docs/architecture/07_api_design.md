# API Design — Enterprise Agentic RAG Platform

## Base URL: `/api/v1`

All responses use `application/json`. Streaming uses `text/event-stream` (SSE).
All authenticated endpoints require: `Authorization: Bearer <jwt>` or `X-API-Key: <key>`

---

## 1. Authentication

### `POST /api/v1/auth/token`
Exchange API key for JWT token.

**Request:**
```json
{
  "api_key": "rk_live_..."
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "uuid",
    "email": "user@company.com",
    "role": "editor"
  }
}
```

### `POST /api/v1/auth/refresh`
Exchange refresh token for new access token.

### `DELETE /api/v1/auth/logout`
Revoke session token.

---

## 2. Documents

### `POST /api/v1/documents`
Upload a document for ingestion.

**Request:** `multipart/form-data`
- `file`: binary (PDF, DOCX, TXT, MD, HTML)
- `metadata` (optional JSON): `{"tags": [...], "domain": "HR", "custom": {}}`

**Response `202 Accepted`:**
```json
{
  "document_id": "uuid",
  "file_name": "policy.pdf",
  "file_type": "pdf",
  "status": "processing",
  "task_id": "uuid",
  "created_at": "2026-06-15T10:00:00Z"
}
```

### `GET /api/v1/documents`
List documents with pagination and filtering.

**Query params:** `page=1&size=20&status=indexed&domain=HR&file_type=pdf&search=policy`

**Response `200`:**
```json
{
  "items": [
    {
      "id": "uuid",
      "file_name": "policy.pdf",
      "file_type": "pdf",
      "status": "indexed",
      "page_count": 12,
      "word_count": 5432,
      "domain": "HR",
      "tags": ["policy", "leave"],
      "indexed_at": "2026-06-15T10:05:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "size": 20,
  "pages": 3
}
```

### `GET /api/v1/documents/{document_id}`
Get document detail with metadata.

### `DELETE /api/v1/documents/{document_id}`
Delete document and all associated chunks/vectors.

**Response `200`:**
```json
{
  "document_id": "uuid",
  "deleted_chunks": 87,
  "message": "Document and all vectors deleted."
}
```

### `POST /api/v1/documents/{document_id}/reindex`
Re-run ingestion pipeline on existing document.

### `PATCH /api/v1/documents/{document_id}/metadata`
Update custom metadata.

**Request:**
```json
{
  "tags": ["policy", "updated"],
  "domain": "Legal",
  "custom_metadata": {"owner": "legal-team"}
}
```

### `GET /api/v1/documents/{document_id}/chunks`
List all chunks for a document (with pagination).

---

## 3. Chat

### `POST /api/v1/chat`
Send a query and receive a streaming answer.

**Request:**
```json
{
  "query": "What is the refund policy?",
  "conversation_id": "uuid or null",
  "filters": {
    "domain": "operations",
    "tags": ["returns"],
    "date_range": null
  },
  "stream": true,
  "debug": false
}
```

**Response (SSE stream):**
```
data: {"type": "query_processed", "intent": "policy_lookup", "domain": "operations", "expanded_queries": [...]}

data: {"type": "retrieval_complete", "chunks_count": 10, "latency_ms": 245}

data: {"type": "token", "content": "The "}
data: {"type": "token", "content": "refund "}
data: {"type": "token", "content": "policy..."}

data: {"type": "done", "answer": "...", "citations": [...], "tokens_used": {...}, "latency_ms": 1250}

data: [DONE]
```

**SSE event types:**
| Type | Payload |
|------|---------|
| `query_processed` | intent, domain, expanded_queries, filters |
| `retrieval_complete` | chunks_count, latency_ms |
| `compression_complete` | original_tokens, compressed_tokens |
| `token` | content (streamed token) |
| `done` | full answer, citations, tokens_used, latency_ms |
| `error` | code, message |

### `GET /api/v1/chat/conversations`
List user's conversations.

### `GET /api/v1/chat/conversations/{conversation_id}`
Get full conversation with message history.

### `DELETE /api/v1/chat/conversations/{conversation_id}`
Delete conversation.

---

## 4. Retrieval Inspector

### `POST /api/v1/retrieval/inspect`
Full debug retrieval run — returns all intermediate scores.

**Request:**
```json
{
  "query": "What is the refund policy?",
  "filters": {},
  "include_content": true
}
```

**Response `200`:**
```json
{
  "original_query": "What is the refund policy?",
  "processed_query": {
    "rewritten": "What is the product return and refund policy?",
    "expanded": ["refund process", "return rules", "money back policy"],
    "intent": "policy_lookup",
    "domain": "operations",
    "filters": {"source_tags": ["returns_docs"]}
  },
  "vector_results": [
    {
      "chunk_id": "uuid",
      "content": "...",
      "document_name": "policy.pdf",
      "page_number": 5,
      "vector_score": 0.891,
      "rank": 1
    }
  ],
  "bm25_results": [...],
  "fused_results": [
    {
      "chunk_id": "uuid",
      "vector_score": 0.891,
      "bm25_score": 14.2,
      "rrf_score": 0.032,
      "rank": 1
    }
  ],
  "reranked_results": [
    {
      "chunk_id": "uuid",
      "rerank_score": 0.967,
      "final_rank": 1
    }
  ],
  "latency_breakdown": {
    "query_processing_ms": 320,
    "vector_search_ms": 45,
    "bm25_search_ms": 38,
    "fusion_ms": 2,
    "reranking_ms": 180,
    "total_ms": 585
  }
}
```

---

## 5. Evaluation

### `POST /api/v1/evaluation/runs`
Trigger an evaluation run.

**Request:**
```json
{
  "name": "weekly_eval_2026_06_15",
  "dataset_name": "golden_set_v1",
  "evaluators": ["ragas", "deepeval"]
}
```

**Response `202`:**
```json
{
  "run_id": "uuid",
  "status": "running",
  "total_items": 50
}
```

### `GET /api/v1/evaluation/runs`
List all evaluation runs.

### `GET /api/v1/evaluation/runs/{run_id}`
Get evaluation run results.

**Response `200`:**
```json
{
  "run_id": "uuid",
  "name": "weekly_eval_2026_06_15",
  "status": "completed",
  "metrics": {
    "faithfulness": 0.87,
    "answer_relevancy": 0.91,
    "context_precision": 0.84,
    "context_recall": 0.79,
    "hallucination_score": 0.08
  },
  "total_items": 50,
  "completed_at": "2026-06-15T11:00:00Z",
  "model_config": {...}
}
```

### `GET /api/v1/evaluation/trends`
Get metric trends over time.

**Query params:** `metric=faithfulness&days=30`

---

## 6. Feedback

### `POST /api/v1/feedback`
Submit user feedback on a message.

**Request:**
```json
{
  "message_id": "uuid",
  "rating": 5,
  "comment": "Perfect answer",
  "feedback_tags": ["good"]
}
```

**Response `201`:**
```json
{
  "feedback_id": "uuid",
  "message": "Feedback recorded."
}
```

---

## 7. Admin

### `GET /api/v1/admin/settings`
Get all system settings. (Admin role required)

### `PUT /api/v1/admin/settings/{key}`
Update a system setting.

**Request:**
```json
{
  "value": {"provider": "anthropic", "model": "claude-sonnet-4-6"}
}
```

### `GET /api/v1/admin/api-keys`
List all API keys for current user.

### `POST /api/v1/admin/api-keys`
Create a new API key.

**Request:**
```json
{"name": "production-key"}
```

**Response `201`:**
```json
{
  "key": "rk_live_...",
  "key_id": "uuid",
  "name": "production-key"
}
```

**Note:** raw key returned only once.

### `DELETE /api/v1/admin/api-keys/{key_id}`
Revoke an API key.

---

## 8. Health & Metrics

### `GET /api/v1/health`
System health check.

**Response `200`:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "checks": {
    "postgres": "ok",
    "qdrant": "ok",
    "elasticsearch": "ok",
    "redis": "ok"
  },
  "uptime_seconds": 86400
}
```

### `GET /api/v1/metrics`
Prometheus metrics endpoint (text/plain).

---

## 9. Error Response Format

All errors follow RFC 7807 Problem Details:

```json
{
  "type": "https://api.ragplatform.com/errors/validation",
  "title": "Validation Error",
  "status": 422,
  "detail": "File type 'csv' is not supported.",
  "instance": "/api/v1/documents",
  "request_id": "req_abc123",
  "timestamp": "2026-06-15T10:00:00Z"
}
```

**Standard error codes:**
| HTTP Status | type slug | When |
|------------|-----------|------|
| 400 | `bad-request` | Malformed input |
| 401 | `unauthorized` | Missing/invalid auth |
| 403 | `forbidden` | Insufficient role |
| 404 | `not-found` | Resource not found |
| 409 | `conflict` | Duplicate resource |
| 422 | `validation` | Schema validation failure |
| 429 | `rate-limited` | Too many requests |
| 500 | `internal` | Unexpected server error |
| 503 | `service-unavailable` | Dependency down |

---

## 10. Rate Limits

| Endpoint Group | Limit |
|----------------|-------|
| POST /chat | 30 req/min per user |
| POST /documents | 10 req/min per user |
| POST /evaluation/runs | 5 req/hour per user |
| GET /retrieval/inspect | 60 req/min per user |
| All other GET | 200 req/min per user |

Rate limit headers returned on all responses:
```
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 28
X-RateLimit-Reset: 1718449200
Retry-After: 45  (only on 429)
```
