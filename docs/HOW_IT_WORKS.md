# How the RAG System Works — Complete Guide
> Easy, plain-English explanation of every component, flow, and alternative.

---

## 1. The Big Picture — What Services Run

```
Your Browser
    │
    ▼
┌─────────────────────────────┐
│  Streamlit UI  :8501        │  ← the dashboard you see and click
│  src/ui/                    │
└────────────┬────────────────┘
             │ HTTP calls
             ▼
┌─────────────────────────────┐
│  FastAPI Backend  :8000     │  ← the brain / orchestrator
│  src/api/ + src/retrieval/  │
└──┬──────┬──────┬────────────┘
   │      │      │
   ▼      ▼      ▼
Postgres Qdrant  Redis        ← storage layer (explained below)
:5432    :6333   :6379
              +
         Elasticsearch
              :9200
```

---

## 2. The 4 Storage Services — What Each One Stores

| Service | Port | What it stores | Why this one? |
|---|---|---|---|
| **PostgreSQL** | 5432 | Documents, chunk records, users, eval runs/metrics | Structured data, source of truth, SQL queries |
| **Qdrant** | 6333 | Vector embeddings (3072 floats per chunk) | Fast similarity / semantic search |
| **Elasticsearch** | 9200 | Same chunks as BM25 inverted index | Fast keyword / exact-word search |
| **Redis** | 6379 | Cached embeddings, semantic query cache | Speed — avoid re-calling OpenAI for same text |

**Alternatives used in industry:**
| Current | Alternative |
|---|---|
| Qdrant | Pinecone, Weaviate, Chroma, pgvector (Postgres extension) |
| Elasticsearch | OpenSearch, Solr, Typesense |
| Redis | Memcached, DynamoDB (for cache), Valkey |
| PostgreSQL | MySQL, MongoDB, Supabase |

---

## 3. FLOW 1 — Uploading a Document (8 Stations)

```
You upload: my_document.pdf
                │
                ▼
FastAPI  POST /api/v1/documents
                │
                ├─ Save file to  ./uploads/
                ├─ Write doc row to PostgreSQL  (status = "pending")
                └─ Fire background task  ───────────────────────────┐
                                                                    │
                                                                    ▼
                                            ┌──────────────────────────────────┐
                                            │       IngestionPipeline          │
                                            │  src/ingestion/pipeline.py       │
                                            └──────────────────────────────────┘
```

### Station 1 — DoclingLoader (Reads the File)

```
my_document.pdf
      │
      ▼
DoclingLoader   ← tries first (best for PDFs, DOCX)
      │
      ├─ extracts raw text page by page
      ├─ detects tables → converts to markdown
      └─ counts pages + words

Output: RawDocument {
  full_text:   "Chapter 1: Introduction...",
  text_blocks: [ {text: "...", page: 1}, ... ],
  tables:      [ {markdown: "|Col1|Col2|", page: 3} ],
  page_count:  42,
  word_count:  15000
}
```

**If DoclingLoader fails** → falls back to **UnstructuredLoader** (different parsing library, same output format).

**Alternatives:** LlamaParse (cloud, paid), PyMuPDF, pdfplumber, Apache Tika

---

### Station 2 — LLMMetadataEnricher (GPT-4o-mini Tags the Document)

Takes only the **first 2000 characters** and sends this prompt to **GPT-4o-mini**:

```
"You are a document analyst. Extract structured metadata. Return ONLY JSON:
 - summary:  2-3 sentence summary
 - tags:     3-8 keyword tags
 - domain:   one of [HR, Legal, Finance, Engineering, ...]
 - language: "en"
 - entities: [{name, type}]

Document (first 2000 chars):
Chapter 1: Machine learning in production..."
```

GPT-4o-mini replies:
```json
{
  "summary": "A guide to deploying ML models in production...",
  "tags": ["machine learning", "production", "monitoring"],
  "domain": "Engineering",
  "language": "en",
  "entities": [{"name": "TensorFlow", "type": "product"}]
}
```

Saved to **PostgreSQL** as document metadata. Used later for filtered search ("only Engineering docs").

**Alternatives for enrichment:** Anthropic Claude Haiku, Gemini Flash, local Llama 3 (via Ollama)

---

### Station 3 — ParentChildChunker (Slices the Text into Two Levels)

**Why two levels?**
- **Child chunks** (256 tokens) → small = precise search target
- **Parent chunks** (1024 tokens) → big = full context window for the LLM to answer from

```
Full document text (15,000 tokens)
         │
         ▼  split every 1024 tokens, 32-token overlap
┌──────────────────────────────────────────────┐
│ PARENT 1  tokens 0    → 1024                 │
│ PARENT 2  tokens 992  → 2016  (32 overlap)   │
│ PARENT 3  tokens 1984 → 3008                 │
│ ...                                          │
└──────────────────────────────────────────────┘
         │
         ▼  each parent is split again every 256 tokens, 32 overlap
┌──────────────────────────────────────────────┐
│  parent 1  →  child 1a  (0–256)              │  ← searched
│              child 1b  (224–480)             │  ← searched
│              child 1c  (448–704)             │  ← searched
│              child 1d  (672–928)             │  ← searched
│  parent 2  →  child 2a ...                   │
└──────────────────────────────────────────────┘
         +
┌──────────────────────────────────────────────┐
│ TABLE chunks (one per detected table)        │  ← searched
│  stored as markdown text                     │
└──────────────────────────────────────────────┘
```

A 42-page PDF → ~18 parents + ~72 children + 8 tables = **~98 chunk objects**

**Alternatives:** Fixed-size chunking, sentence-based chunking, semantic chunking (LangChain), recursive chunking

---

### Station 4 — OpenAIEmbedder (Converts Text to Numbers)

Only **child + table chunks** get embedded (parents are never searched directly).

```
child 1a: "ML models fail when training data differs..."
child 1b: "Monitoring data drift requires statistical tests..."
...

          ↓  one batched API call to OpenAI  ↓
      model: text-embedding-3-large
      dimensions: 3072
      YOUR OPENAI_API_KEY

child 1a → [ 0.023, -0.451,  0.881, ... ]  ← 3072 numbers
child 1b → [ 0.112,  0.334, -0.229, ... ]
child 1c → [-0.445,  0.772,  0.003, ... ]
```

These 3072 numbers = the chunk's meaning in math. Similar meaning = similar numbers.

**Alternatives:**
| Current | Alternative | Notes |
|---|---|---|
| text-embedding-3-large | text-embedding-3-small | cheaper, 1536 dims |
| text-embedding-3-large | BAAI/bge-m3 | free, local, multilingual |
| text-embedding-3-large | intfloat/e5-large-v2 | free, local |
| text-embedding-3-large | Cohere embed-v3 | cloud, paid |

---

### Stations 5–8 — Save Everything to the 3 Stores

```
All chunks (~98 total)
         │
         ├──► PostgreSQL    ← every chunk row (content, page, position, token_count...)
         │
         ├──► Qdrant        ← only child+table chunks WITH their 3072-float vectors
         │                    (what semantic search will query)
         │
         └──► Elasticsearch ← all chunks as BM25 text documents
                               (what keyword search will query)

Then:
PostgreSQL doc row: "pending" → "processing" → "indexed" ✅
```

---

## 4. FLOW 2 — Chat / Asking a Question (7 Stages, A→G)

```
You type: "How does ML fail in production?"
                │
                ▼
Streamlit  POST /api/v1/chat  {stream: true}
                │
                ▼
          QueryPipeline
```

### Stage A — QueryAgent (4 LLM Calls to Understand the Question)

```
Your question: "How does ML fail in production?"
                │
       ┌────────┴────────────────────────────────┐
       │                                          │
       ▼                                          ▼
  QueryRewriter                            IntentClassifier
  GPT-4o-mini                              GPT-4o-mini
  "Please tell me how                      intent = "analytical"
   machine learning                        domain = "Engineering"
   can fail in production."
       │                                          │
       ▼                                          ▼
  QueryExpander                            FilterGenerator
  GPT-4o-mini                              GPT-4o-mini
  generates 3 variants:                    {domain: "Engineering",
  1. "How can ML systems fail?"             tags: ["machine learning",
  2. "What causes ML failure?"              "production"]}
  3. "ML deployment failure modes"
```

Now we have **4 queries** (original + 3 variants) and **metadata filters**.

---

### Stage B — HybridRetriever (Two Searches in Parallel)

```
4 queries + filters
       │
       ├────────────────────────────────────────────┐
       │                                            │
       ▼                                            ▼
  VectorSearcher                              BM25Searcher
  (Qdrant)                                    (Elasticsearch)

  embed all 4 queries with                   keyword search for
  text-embedding-3-large                     all 4 queries

  find the 20 chunks whose                   find the 20 chunks
  3072 numbers are most                      with highest term
  similar to each query                      frequency scores

  Result: top 20 chunks                      Result: top 20 chunks
  (semantic matches)                          (keyword matches)

       └────────────────────┬───────────────────────┘
                            ▼
                    Two separate lists of 20 chunks each
```

---

### Stage C — Fuser (Merge Two Lists Using RRF)

**The problem:** Vector scores and BM25 scores are on different scales — you can't add them.

**RRF Solution:** Ignore raw scores. Only use **rank position**.

```
Formula: score(chunk) = 1 / (60 + rank)   for each list it appears in
         (60 = constant "k", stops #1 from dominating everything)
```

**Example with real math:**

```
chunk_A appeared in BOTH lists:
  Vector list: rank 1  →  1/(60+1) = 0.01639
  BM25 list:   rank 2  →  1/(60+2) = 0.01613
  TOTAL:                 0.03252   ← BONUS for appearing in both

chunk_B appeared in Vector list only:
  Vector list: rank 2  →  1/(60+2) = 0.01613
  TOTAL:                 0.01613   ← no bonus

chunk_A WINS — both searches agreed on it = most reliable result
```

**3 sub-steps inside the Fuser:**

```
20 vector chunks + 20 BM25 chunks
         │
         ▼  Step 1: RRFFusion
    Merge all unique chunks (some overlap)
    Add 1/(60+rank) per list appearance
    Sort by total RRF score
    Result: 35–39 unique chunks
         │
         ▼  Step 2: ScoreNormalizer
    Rescale raw scores to 0–1 range
    (display only, does NOT change ranking)
         │
         ▼  Step 3: DuplicateRemover
    Remove chunks with identical content_hash
    (overlap from parent/child chunks)
    Result: ~35 unique chunks, sorted by RRF
```

---

### Stage D — Reranker (Pick the Best 10)

**Original design (BGE cross-encoder):**
```
35 chunks + query → BAAI/bge-reranker-large (567MB model)
                    reads query AND chunk TOGETHER
                    scores: "how relevant is this chunk to THIS query?"
                    Takes ~115 seconds on CPU ❌ → killed by Docker timeout
```

**Current design (Passthrough):**
```python
top = sorted(candidates, key=lambda c: c.rrf_score)[:10]
```

Just takes the top 10 by RRF score. < 1ms. RRF is already a strong signal.

**Comparison:**

| | BGE Reranker | Passthrough (current) | Cohere Reranker |
|---|---|---|---|
| What it does | AI reads query+chunk together | Sort by RRF score | Cloud AI API call |
| Time | 115s (CPU) / 1s (GPU) | < 1ms | ~1-2s |
| Quality | Best | Good | Very good |
| Cost | GPU or very slow | Free | Paid API |
| Use when | You have a GPU | Dev / no GPU | Prod without GPU |

To switch: change `RERANKER_PROVIDER=` in `.env`
- `passthrough` → current (fast, no GPU needed)
- `bge` → local model (needs GPU)
- `cohere` → cloud API (add `COHERE_API_KEY`)

---

### Stage E — ContextProcessor (Compress + Clean the 10 Chunks)

```
10 chunks (selected by reranker)
         │
         ▼  ContextDeduplicator
    Remove near-duplicate chunks
         │
         ▼  ContextCompressor  (GPT-4o-mini)
    For each chunk, extract only sentences
    directly relevant to the query
    (reduces tokens, improves answer quality)
         │
         ▼  TokenBudgetManager
    Make sure total context fits in 6000 tokens
    (GPT-4o context limit management)
         │
         ▼  CitationPreserver
    Assign [1], [2], [3]... citation numbers
    to each chunk for referencing in answer
```

---

### Stage G — AnswerPipeline (Generate the Streaming Answer)

```
compressed chunks + citations + query
         │
         ▼  ContextAssembler
    Build context block:
    "[1] ...chunk text...
     [2] ...chunk text..."
         │
         ▼  PromptBuilder
    Build final prompt:
    "Using ONLY the context below, answer the question.
     Context: [1] ... [2] ...
     Question: How does ML fail in production?
     Answer:"
         │
         ▼  StreamGenerator → GPT-4o (large model)
    Streams tokens one by one via SSE:
    {"type": "token", "content": "Machine"}
    {"type": "token", "content": " learning"}
    {"type": "token", "content": " can"}
    ...
    {"type": "done", "answer": "...", "citations": [...]}
         │
         ▼ Streamlit receives each token → updates chat bubble word by word
```

---

### Semantic Cache (Runs BEFORE Stage A)

```
Your question → embed it → search Redis/Qdrant for similar past questions
                │
                ├─ similarity score > 0.95? → return cached answer instantly ⚡
                │   (skip ALL stages A→G)
                │
                └─ not found? → run full pipeline → store result in cache
```

---

## 5. FLOW 3 — Evaluation (How Good Are the Answers?)

```
Trigger Evaluation in UI
         │
         ▼  POST /api/v1/evaluation/runs
         │
         └─ background task ─────────────────────────────────────────┐
                                                                     ▼
                                             For each question in dataset:

                                             1. pipeline.inspect()
                                                → get retrieved chunks (contexts)

                                             2. pipeline.answer()
                                                → get generated answer

                                             3. GPT-4o-mini scores:
                                                Faithfulness      = answer grounded in chunks?
                                                Answer Relevancy  = does it answer the question?
                                                Context Relevancy = are chunks relevant?
                                                Answer Correctness= matches ground truth?
                                                (all scored 0→1)

                                             4. Save to PostgreSQL
                                                (evaluation_runs + evaluation_metrics tables)

UI polls GET /api/v1/evaluation/runs → shows progress → bar chart when done
```

**Alternatives for evaluation:**
| Current | Alternative |
|---|---|
| LLM-based scoring | RAGAS library (also LLM-based, more metrics) |
| LLM-based scoring | DeepEval library |
| GPT-4o-mini scorer | Claude Haiku, Gemini Flash |

---

## 6. Which OpenAI API Calls Happen Per Chat Message

```
OPENAI_API_KEY is used for:

1. QueryRewriter        → gpt-4o-mini  (rewrite question)
2. QueryExpander        → gpt-4o-mini  (generate 3 variants)
3. IntentClassifier     → gpt-4o-mini  (detect domain + intent)
4. FilterGenerator      → gpt-4o-mini  (extract metadata filters)
5. Embed query          → text-embedding-3-large
6. ContextCompressor    → gpt-4o-mini  (compress each chunk)
7. AnswerPipeline       → gpt-4o       (stream final answer)

Total per chat message: ~6-8 API calls
```

---

## 7. Environment Variables — What Controls What

```ini
# Which LLM provider is active
SMALL_LLM_PROVIDER=openai   # stages A, context compression, enrichment
LARGE_LLM_PROVIDER=openai   # stage G answer generation

# Which models
OPENAI_SMALL_MODEL=gpt-4o-mini
OPENAI_LARGE_MODEL=gpt-4o

# Which reranker (D stage)
RERANKER_PROVIDER=passthrough   # passthrough | bge | cohere

# Which embedding model
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_MODEL=text-embedding-3-large

# How many results each search returns
VECTOR_SEARCH_TOP_K=20   # Qdrant returns this many
BM25_SEARCH_TOP_K=20     # Elasticsearch returns this many
RERANK_TOP_N=10          # final chunks passed to LLM
```

---

## 8. Summary — One Line Per Component

| Component | File | Does what |
|---|---|---|
| DoclingLoader | `src/ingestion/loaders/` | PDF → raw text |
| LLMMetadataEnricher | `src/ingestion/enrichers/` | Tag document with GPT-4o-mini |
| ParentChildChunker | `src/ingestion/chunkers/` | Split into parent (1024) + child (256) chunks |
| OpenAIEmbedder | `src/ingestion/embedders/` | Text → 3072-number vectors |
| QueryAgent | `src/retrieval/agents/` | Rewrite + expand + classify query with LLM |
| HybridRetriever | `src/retrieval/hybrid_retriever.py` | Search Qdrant + Elasticsearch in parallel |
| Fuser (RRF) | `src/retrieval/fusers/` | Merge two result lists by rank, not score |
| Reranker | `src/retrieval/rerankers/` | Pick top 10 (passthrough = by RRF, bge = by AI) |
| ContextProcessor | `src/retrieval/context/` | Compress + deduplicate + fit in token budget |
| AnswerPipeline | `src/retrieval/answer/` | Stream answer word-by-word from GPT-4o |
| SemanticCache | `src/retrieval/cache/` | Skip pipeline if same question asked before |
| EvaluationRunner | `src/evaluation/offline/` | Score answers on faithfulness/relevancy |
