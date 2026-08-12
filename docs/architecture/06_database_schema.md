# Database Schema — Enterprise Agentic RAG Platform

## 1. PostgreSQL Schema

### 1.1 Users & Auth

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255),              -- NULL if API-key-only
    role            VARCHAR(50) NOT NULL DEFAULT 'viewer',  -- admin|editor|viewer
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);

CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash        VARCHAR(255) NOT NULL UNIQUE,  -- sha256 of raw key
    name            VARCHAR(100) NOT NULL,
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX idx_api_keys_user_id  ON api_keys(user_id);
```

---

### 1.2 Documents

```sql
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_name       VARCHAR(500) NOT NULL,
    file_type       VARCHAR(50) NOT NULL,              -- pdf|docx|txt|md|html
    file_size_bytes BIGINT NOT NULL,
    file_path       VARCHAR(1000),                     -- local or S3 path
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
                                                       -- pending|processing|indexed|failed
    error_message   TEXT,
    page_count      INTEGER,
    word_count      INTEGER,
    loader_used     VARCHAR(100),                      -- docling|unstructured|llamaparse
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at      TIMESTAMPTZ
);

CREATE INDEX idx_documents_user_id  ON documents(user_id);
CREATE INDEX idx_documents_status   ON documents(status);
CREATE INDEX idx_documents_file_type ON documents(file_type);

CREATE TABLE document_metadata (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE UNIQUE,
    summary         TEXT,
    tags            TEXT[],                            -- domain tags
    domain          VARCHAR(100),                      -- HR|Legal|Finance|Ops|General
    language        VARCHAR(20) DEFAULT 'en',
    entities        JSONB DEFAULT '[]',                -- [{name, type, confidence}]
    custom_metadata JSONB DEFAULT '{}',                -- user-provided key-value
    enriched_at     TIMESTAMPTZ,
    enrichment_model VARCHAR(100)
);

CREATE INDEX idx_doc_metadata_document_id ON document_metadata(document_id);
CREATE INDEX idx_doc_metadata_domain      ON document_metadata(domain);
CREATE INDEX idx_doc_metadata_tags        ON document_metadata USING GIN(tags);
```

---

### 1.3 Chunks

```sql
CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_chunk_id UUID REFERENCES document_chunks(id) ON DELETE CASCADE,
    chunk_type      VARCHAR(20) NOT NULL DEFAULT 'child',  -- parent|child|table|standalone
    content         TEXT NOT NULL,
    content_hash    VARCHAR(64) NOT NULL,              -- sha256 for dedup
    position        INTEGER NOT NULL,                  -- ordering within document
    token_count     INTEGER,
    page_number     INTEGER,
    section         VARCHAR(500),
    contains_table  BOOLEAN DEFAULT false,
    embedding_model VARCHAR(100),
    qdrant_point_id UUID,                              -- reference to Qdrant
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunks_document_id     ON document_chunks(document_id);
CREATE INDEX idx_chunks_parent_id       ON document_chunks(parent_chunk_id);
CREATE INDEX idx_chunks_content_hash    ON document_chunks(content_hash);
CREATE INDEX idx_chunks_page_number     ON document_chunks(page_number);
```

---

### 1.4 Conversations

```sql
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(500),
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversations_user_id ON conversations(user_id);

CREATE TABLE messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role                VARCHAR(20) NOT NULL,           -- user|assistant
    content             TEXT NOT NULL,
    citations           JSONB DEFAULT '[]',             -- [{index, source, doc, chunk_id, page}]
    retrieved_chunks    JSONB DEFAULT '[]',             -- debug: [{chunk_id, scores}]
    model_used          VARCHAR(100),
    tokens_used         JSONB,                          -- {prompt, completion, total}
    latency_ms          INTEGER,
    langfuse_trace_id   VARCHAR(255),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_messages_role            ON messages(role);
```

---

### 1.5 Evaluation

```sql
CREATE TABLE evaluation_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    dataset_name    VARCHAR(255) NOT NULL,
    triggered_by    VARCHAR(50) DEFAULT 'manual',      -- manual|scheduled
    user_id         UUID REFERENCES users(id),
    model_config    JSONB NOT NULL,                    -- {small_llm, large_llm, embedder, reranker}
    retrieval_config JSONB NOT NULL,
    status          VARCHAR(50) DEFAULT 'running',     -- running|completed|failed
    total_items     INTEGER DEFAULT 0,
    completed_items INTEGER DEFAULT 0,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    error_message   TEXT
);

CREATE TABLE evaluation_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    metric_name     VARCHAR(100) NOT NULL,             -- faithfulness|relevancy|precision|recall|hallucination
    value           FLOAT NOT NULL,
    aggregation     VARCHAR(20) DEFAULT 'mean',        -- mean|median|p95
    sample_size     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_eval_metrics_run_id     ON evaluation_metrics(run_id);
CREATE INDEX idx_eval_metrics_name       ON evaluation_metrics(metric_name);

CREATE TABLE evaluation_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    generated_answer TEXT,
    ground_truth    TEXT,
    retrieved_contexts JSONB DEFAULT '[]',
    faithfulness    FLOAT,
    answer_relevancy FLOAT,
    context_precision FLOAT,
    context_recall  FLOAT,
    hallucination_score FLOAT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_eval_items_run_id ON evaluation_items(run_id);
```

---

### 1.6 Feedback

```sql
CREATE TABLE user_feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id),
    rating          SMALLINT NOT NULL,                 -- 1 (thumbs_down) | 5 (thumbs_up)
    comment         TEXT,
    feedback_tags   TEXT[],                            -- wrong|incomplete|hallucinated|good
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feedback_message_id ON user_feedback(message_id);
CREATE INDEX idx_feedback_user_id    ON user_feedback(user_id);
CREATE INDEX idx_feedback_rating     ON user_feedback(rating);
```

---

### 1.7 System Settings

```sql
CREATE TABLE system_settings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key             VARCHAR(255) NOT NULL UNIQUE,
    value           JSONB NOT NULL,
    description     TEXT,
    updated_by      UUID REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed defaults
INSERT INTO system_settings (key, value, description) VALUES
  ('small_llm',        '{"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}', 'Small LLM for query rewriting, compression'),
  ('large_llm',        '{"provider": "anthropic", "model": "claude-sonnet-4-6"}',         'Large LLM for answer generation'),
  ('embedding_model',  '{"provider": "openai",    "model": "text-embedding-3-large"}',    'Embedding model'),
  ('reranker',         '{"provider": "bge",       "model": "BAAI/bge-reranker-large"}',   'Reranker model'),
  ('vector_top_k',     '20',                                                               'Vector search top-K'),
  ('bm25_top_k',       '20',                                                               'BM25 search top-K'),
  ('rerank_top_n',     '10',                                                               'Final top-N after reranking'),
  ('chunk_parent_size','1024',                                                              'Parent chunk token size'),
  ('chunk_child_size', '256',                                                              'Child chunk token size'),
  ('chunk_overlap',    '32',                                                               'Token overlap between chunks');
```

---

## 2. Qdrant Collection Schema

### Collection: `document_chunks`

```json
{
  "collection_name": "document_chunks",
  "vectors_config": {
    "size": 3072,
    "distance": "Cosine"
  },
  "payload_schema": {
    "chunk_id":       "keyword",
    "document_id":    "keyword",
    "user_id":        "keyword",
    "content":        "text",
    "chunk_type":     "keyword",
    "domain":         "keyword",
    "tags":           "keyword[]",
    "page_number":    "integer",
    "contains_table": "bool",
    "token_count":    "integer",
    "indexed_at":     "datetime",
    "file_type":      "keyword",
    "language":       "keyword"
  }
}
```

**Indexes created:**
- `domain` — keyword index for metadata filtering
- `tags` — keyword index for tag-based filtering
- `user_id` — for multi-tenant isolation
- `file_type` — for type-based filtering

---

## 3. Elasticsearch Index Schema

### Index: `document_chunks`

```json
{
  "mappings": {
    "properties": {
      "chunk_id":       { "type": "keyword" },
      "document_id":    { "type": "keyword" },
      "user_id":        { "type": "keyword" },
      "content":        {
        "type": "text",
        "analyzer": "english",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "domain":         { "type": "keyword" },
      "tags":           { "type": "keyword" },
      "page_number":    { "type": "integer" },
      "contains_table": { "type": "boolean" },
      "token_count":    { "type": "integer" },
      "indexed_at":     { "type": "date" },
      "file_type":      { "type": "keyword" },
      "language":       { "type": "keyword" }
    }
  },
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1,
    "analysis": {
      "analyzer": {
        "english": {
          "type": "english"
        }
      }
    }
  }
}
```

---

## 4. Redis Key Patterns

| Key Pattern | TTL | Value | Purpose |
|-------------|-----|-------|---------|
| `embedding:{sha256}` | 24h | JSON array (vector) | Embedding cache |
| `query:{sha256}` | 60min | JSON (full response) | Query result cache |
| `retrieval:{sha256}` | 30min | JSON (chunks list) | Retrieval cache |
| `session:{user_id}:{token_hash}` | 1h | JSON (user info) | Session cache |
| `ratelimit:{user_id}:{window}` | 60s | Integer (count) | Rate limit counter |
| `doc_status:{document_id}` | 24h | String (status) | Ingestion progress |

---

## 5. Entity Relationship Summary

```
users ──────────────────┐
  │                     │
  ├── api_keys          │
  ├── documents ────────┤
  │     └── document_metadata
  │     └── document_chunks (self-ref: parent/child)
  ├── conversations      │
  │     └── messages ───┤
  │           └── user_feedback
  └── evaluation_runs ──┘
        ├── evaluation_metrics
        └── evaluation_items

system_settings (global, no FK)
```
