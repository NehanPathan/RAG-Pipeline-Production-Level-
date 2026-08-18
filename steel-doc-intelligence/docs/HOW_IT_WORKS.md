# How the RAG System Works — Complete Guide
> Easy, plain-English explanation of every component, flow, and alternative.

> **Phase 4A (Document Intelligence upgrade):** OCR, layout extraction, hybrid chunking, and
> dual-role embeddings described below were added on top of the original Phase 1-3 pipeline.
> See `docs/architecture/12_phase4a_design_review.md` for the full design review and module log.

## Contents

1. [The Big Picture](#1-the-big-picture--what-services-run) — what services run
2. [Storage Services](#2-the-4-storage-services--what-each-one-stores) — what each one stores
3. [Flow 1 — Uploading a Document](#3-flow-1--uploading-a-document-worked-example) — full worked example, station by station
4. [Flow 2 — Chat](#4-flow-2--chat--asking-a-question-7-stages-ag) — asking a question, A→G
5. [Flow 3 — Evaluation](#5-flow-3--evaluation-how-good-are-the-answers) — scoring answer quality
6. [What Each Chat Message Actually Costs](#6-what-each-chat-message-actually-costs)
7. [What Each Document Upload Actually Costs](#7-what-each-document-upload-actually-costs)
8. [Environment Variables](#8-environment-variables--what-controls-what)
9. [Component Summary Table](#9-summary--one-line-per-component)
10. [Troubleshooting — Things We Actually Hit](#10-troubleshooting--things-we-actually-hit-running-this)

**New here? Read section 3 first** — it follows one real 4-paragraph refund-policy document
through every single station, with the actual output at each step, so you can see exactly what
transforms into what before diving into any one component's details.

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

## 3. FLOW 1 — Uploading a Document (Worked Example)

> **The example we'll follow through every station below** is a real 4-section markdown policy
> document, actually uploaded and ingested during Phase 4A testing — not a hypothetical. Small
> enough to show in full, but it exercises headings, lists, a table, and chunk deduplication all
> at once:
> ```markdown
> # Refund Policy
>
> ## Overview
> Customers may request a refund within 30 days of purchase for most products
> sold through our platform. This document explains the process end to end.
>
> ## Eligibility
> - The item must be unused and in its original packaging.
> - A valid proof of purchase is required.
> - Digital goods are only refundable if not yet downloaded.
>
> ## Process
> 1. Submit a refund request through the customer portal.
> 2. Wait for approval from the support team, typically within 2 business days.
> 3. Once approved, funds are returned to the original payment method within 5-7 business days.
>
> ## Exceptions
> Perishable goods, custom-made items, and gift cards are not eligible for
> refunds under any circumstances.
>
> | Reason | Timeline |
> |---|---|
> | Defective item | 30 days |
> | Changed mind | 14 days |
> | Wrong item shipped | 60 days |
> ```
> Every "🔍 In our example" box below shows this exact document's real output at that station —
> pulled from an actual `GET /documents/{id}/intelligence` and `GET /documents/{id}/chunks` call,
> not simulated.

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
**Images** (png/jpg/tiff/bmp) → **ImagePassthroughLoader** (no native text at all; OCR is the only source of text — see Station 1b).

**Alternatives:** LlamaParse (cloud, paid), PyMuPDF, pdfplumber, Apache Tika

> 🔍 **In our example:** `.md` isn't in DoclingLoader's supported extensions (`.pdf/.docx/.doc`
> only), so **UnstructuredLoader** handles it instead — same output shape, different library.
> It produced one `TextBlock` per heading/paragraph/list-item, each correctly labeled
> (`title`, `text`, `list_item`) and the table extracted separately. `page_count` came back
> `null` — markdown has no concept of pages, so page numbers are `None` throughout this
> example's whole journey (that's expected, not a bug).

---

### Station 1b — OCRDetector + OCRProvider (Skip or Recognize)

```
RawDocument (from Station 1)
      │
      ▼
OCRDetector.detect()   ← checks EACH page individually, not a document-wide average
      │
      ├─ .docx / .md / .html / .txt          → always skip (never scanned)
      ├─ image file (no extractable text)     → always OCR (page 1)
      └─ .pdf → for each page: words_on_page < 10  → that page needs OCR
                                words_on_page ≥ 10  → that page is fine as-is
      │
      ▼ (only if ≥1 page needs OCR)
OCRProvider.recognize(file_path, pages=[...])   ← TesseractProvider by default (local, CPU,
      │                                            pytesseract + poppler); only the flagged
      │                                            pages are actually OCR'd
      merges OCR text into ONLY the flagged pages -- other pages' original text (and any
      structural labels DoclingLoader/UnstructuredLoader already attached) are left untouched
      records OCRMetadata: engine, confidence, processing time, language, page count
```

Checking per page (not a whole-document average) matters for mixed documents: a 10-page report with
9 text-rich pages and 1 scanned page would average out fine and silently skip the one page that
actually needed OCR if judged as a whole. Note that Docling also runs its *own* internal OCR engine
automatically on anything it classifies as scanned/image content during `converter.convert()` --
`OCRDetector`/`OCRProvider` here are a *supplemental* layer, only engaged when a page still looks
sparse *after* Docling's own extraction.

**Alternatives:** `OCR_PROVIDER=paddle` (PaddleOCR — interface-complete, not wired for production in
this CPU-only environment, see `PaddleOCRProvider`'s docstring), `OCR_PROVIDER=baidu_unlimited`
(Baidu's cloud General Text Recognition API — interface-complete but untested, no credentials on
hand).

> 🔍 **In our example:** `.md` matches the "always text-native" rule (Station 1b's first check) —
> OCR is skipped immediately, no per-page density check even runs. Real recorded result:
> `ocr_engine: "none"`, `ocr_ran: false`. Contrast with a real 2-page tax-form PDF also tested in
> this project: Docling's *own internal* OCR engine (RapidOCR) already read a nearly-full-page
> scanned image on page 2 before our `OCRDetector` even looked at it — so even that document came
> back `ocr_ran: false` too, correctly, because there was nothing left for the supplemental layer
> to do. `ocr_ran: false` means "the extra OCR layer wasn't needed," not "no text was ever read
> from any image."

> 🔍 **Proof our OCR layer actually works (not just Docling's):** neither example above ever
> exercised `TesseractProvider` for real, since Docling always had enough text first. To prove the
> supplemental layer itself works, we uploaded a plain `.png` image (a screenshot-style image with
> 3 sentences of rendered text, no PDF/Docling involved at all) — that routes 100% through
> `ImagePassthroughLoader` (zero native text, by design) → `OCRDetector` (`required: true`, "image
> file has no extractable text") → `TesseractProvider`. Real recorded result:
> ```json
> { "ocr_engine": "tesseract", "ocr_ran": true,
>   "ocr_confidence_avg": 0.9577, "ocr_processing_time_ms": 1592.5, "ocr_language": "en" }
> ```
> and the extracted chunk content matched the image's text **word-for-word, 30/30 words correct**.
> Getting to this required fixing 3 separate, real bugs first (wrong Python import name for the
> OCR library, missing `tesseract-ocr`/`poppler-utils` system binaries in the Docker image, and
> image file types being rejected by upload validation before ever reaching OCR) — see §10 below
> and the design review's Module Log for the full story. The lesson: **the supplemental OCR layer
> is easy to leave silently broken**, because every realistic PDF test tends to route around it via
> Docling's own internal OCR — only a standalone image upload actually forces it to run.

---

### Station 1c — LayoutAnalyzer (Headings, Tables, Figures, Lists, Forms, Footnotes, Outline)

```
RawDocument (post-OCR if applicable)
      │
      ▼  any text block has a structural label (Docling/Unstructured attach these)?
      ├─ yes → LabeledLayoutAnalyzer   (real structure: headings w/ level, list groups,
      │                                 captions, footnotes, field_key/value → forms)
      └─ no  → HeuristicLayoutAnalyzer (regex/shape fallback: short capitalized line ≈
                                         heading, "- "/"1. " prefix ≈ list item)
      │
      ▼
DocumentLayout { headings, tables, figures, captions, lists, forms, footnotes, outline }
      +
ParsedDocument { raw, ocr_metadata, layout }   ← the one object every stage from here on reads
```

Structure is kept as typed collections, **not** flattened into plain text — this is what Station 3's
HybridChunkingPipeline splits along instead of a fixed token count.

> 🔍 **In our example:** every block came back labeled, so `LabeledLayoutAnalyzer` ran (the
> high-fidelity path). Real recorded `DocumentLayout`:
> ```json
> {
>   "headings": [
>     {"text": "Refund Policy", "level": 0},
>     {"text": "Overview", "level": 1}, {"text": "Eligibility", "level": 1},
>     {"text": "Process", "level": 1}, {"text": "Exceptions", "level": 1}
>   ],
>   "outline": [{"title": "Refund Policy", "level": 0, "children": [
>       {"title": "Overview", "level": 1, "children": []},
>       {"title": "Eligibility", "level": 1, "children": []},
>       {"title": "Process", "level": 1, "children": []},
>       {"title": "Exceptions", "level": 1, "children": []}
>   ]}],
>   "tables_count": 1, "lists_count": 2, "figures_count": 0,
>   "forms_count": 0, "footnotes_count": 0
> }
> ```
> Notice `lists_count: 2`, not 1 — the bullet list (Eligibility) and the numbered list (Process)
> are correctly kept as two *separate* lists because the "Process" heading and "Eligibility"
> paragraph text sit between them structurally, not merged into one giant list.

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

> 🔍 **In our example:** the whole document is under 2000 chars, so GPT-4o-mini saw all of it.
> Real reply: `domain: "Operations"`, `tags: ["refund", "policy", "customer service",
> "eligibility", "process"]` — this is exactly why "only show Operations docs" filtered search
> works later, and it cost about **$0.0002** (see section 7).

---

### Station 3 — HybridChunkingPipeline (Structure → Semantic → Parent/Child → Validate)

Production default since Phase 4A. Four steps, each a separate, swappable component:

```
ParsedDocument (raw text + OCR metadata + layout)
         │
         ▼  Step 1: StructureChunker
    Split along document structure: headings, paragraphs, tables, lists.
    Each paragraph is grouped under its nearest heading; consecutive list
    items merge into one bullet block; tables become their own section.
    Result: N "structural sections", each tagged with a section_title/heading_level.
         │
         ▼  Step 2: SemanticChunker
    Inside each section: split into sentences, embed each one (chunking-role
    embedding model), compute cosine similarity between consecutive sentences.
    Cut wherever similarity drops below an ADAPTIVE threshold —
      threshold = mean(similarities) − stdev(similarities)
    (not a fixed number — it adapts to how varied each section already is).
    Tables and very short sections (<3 sentences) pass through untouched.
         │
         ▼  Step 3: ParentChildChunker.chunk_section()  (same class as before, reused)
    Same two-level parent(1024 tok)/child(256 tok) token-window splitting as
    pre-Phase-4A, just run once per semantic segment instead of once for the
    whole document.
         │
         ▼  Step 4: ChunkValidator
    Reject chunks that are: too small, whitespace-only, exact duplicates,
    OCR garbage (low alphanumeric ratio), or below an OCR-confidence floor.
    Rejection reason is kept (not just a count) for debugging.
         │
         ▼
List[DocumentChunk]  (parent + child + table chunks, same shapes as before)
```

**Why this replaced the old "split every N tokens" approach:** fixed-size splitting doesn't know
where a heading, a table, or a topic shift actually is — it can cut a sentence in half or merge two
unrelated paragraphs into one chunk. HybridChunkingPipeline splits along real structure first, then
real semantic boundaries, and only *then* applies the token-size limits.

**`ParentChildChunker` itself was not rewritten or removed** — it's reused as Step 3, and its
original single-call `chunk()` method still exists unchanged (used by `ParentChildOnlyStrategy`, the
"before" baseline `scripts/benchmark_chunking.py` compares against).

**Alternatives:** `CHUNKING_STRATEGY` isn't a runtime toggle in production (HybridChunkingPipeline is
always used) — `scripts/benchmark_chunking.py` runs both variants side by side to measure the actual
difference. Other approaches: fixed-size chunking (what this project used pre-Phase-4A), sentence-
based chunking, recursive chunking (LangChain-style).

See `docs/architecture/12_phase4a_design_review.md` §5-6 for the full component breakdown.

> 🔍 **In our example:** Step 1 produced 5 structural sections (4 text sections + 1 table). Every
> section was well under 3 sentences or under the 256-token child size, so Step 2 (semantic
> splitting) and Step 3 (parent/child windowing) each passed every section through as a single
> chunk, with NO further splitting needed. Real logged result: `"sections": 5, "segments": 5,
> "chunks_valid": 5, "chunks_rejected": 4`. Those 4 rejected chunks are the interesting part —
> **Step 3 still tried to create a "child" chunk for each section** (that's what it always does),
> but since each section's text was *shorter* than the 256-token child size, the "child" ended up
> containing the exact same text as its own "parent" — Step 4's duplicate check correctly caught
> and dropped all 4, leaving one clean chunk per section:
> ```
> chunk 1: parent, section_title="Overview",    28 tokens
> chunk 2: parent, section_title="Eligibility",  33 tokens
> chunk 3: parent, section_title="Process",      46 tokens
> chunk 4: parent, section_title="Exceptions",   21 tokens
> chunk 5: table,  (the Reason/Timeline table),  21 tokens
> ```
> This is exactly the behavior you'd want — a short document doesn't need artificial extra
> chunking, and the validator prevents duplicate content from reaching the vector store.

---

### Station 4 — OpenAIEmbedder (Converts Text to Numbers)

Only **child + table chunks** get embedded (parents are never searched directly).

**Two separate embedding roles since Phase 4A (`EmbeddingStrategy`):**
- **Chunking role** — powers Station 3's SemanticChunker topic-boundary detection only; defaults to
  local **BAAI/bge-m3** (free, no API cost, runs on CPU). Never stored in Qdrant.
- **Retrieval role** — what actually gets embedded and stored below; still **text-embedding-3-large**
  by default, the exact same singleton instance the query pipeline uses to embed questions (so
  ingestion and search never drift onto different embedding spaces).

```
child 1a: "ML models fail when training data differs..."
child 1b: "Monitoring data drift requires statistical tests..."
...

          ↓  one batched API call to OpenAI (retrieval-role provider)  ↓
      model: text-embedding-3-large
      dimensions: 3072
      YOUR OPENAI_API_KEY

child 1a → [ 0.023, -0.451,  0.881, ... ]  ← 3072 numbers
child 1b → [ 0.112,  0.334, -0.229, ... ]
child 1c → [-0.445,  0.772,  0.003, ... ]
```

These 3072 numbers = the chunk's meaning in math. Similar meaning = similar numbers.

**Alternatives (either role, set independently via `EMBEDDING_PROVIDER` / `CHUNKING_EMBEDDING_PROVIDER`):**
| Current | Alternative | Notes |
|---|---|---|
| text-embedding-3-large | text-embedding-3-small | cheaper, 1536 dims |
| text-embedding-3-large | BAAI/bge-m3 | free, local, multilingual |
| text-embedding-3-large | intfloat/e5-large-v2 | free, local |
| text-embedding-3-large | Cohere embed-v3 | cloud, paid |

> 🔍 **In our example:** recall from Station 3 that all 5 surviving chunks are `parent`/`table`
> type — there are zero `child` chunks (they were all deduped away). Since **only child + table
> chunks get embedded**, that means only the **table chunk** actually got sent to OpenAI here; the
> 4 `parent` chunks show `embedding_model: ""` (empty) in the real API response, and the table
> chunk shows `embedding_model: "text-embedding-3-large"`. This is a real, correct edge case for
> short documents — the parent chunks still exist for potential future context-expansion use, they
> just were never search targets to begin with.

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

> 🔍 **In our example, end to end:** all 5 chunks → PostgreSQL; the 1 embedded table chunk →
> Qdrant; all 5 chunks (parents included — BM25 keyword search benefits from indexing everything,
> unlike vector search) → Elasticsearch; plus one new row in `document_intelligence` (Phase 4A)
> holding the OCR/layout/embedding-model summary from Stations 1b/1c/4. Total real cost for this
> document: **~$0.0004** (enrichment + one table chunk embedded — see section 7). Document status
> went `pending → processing → indexed` in about 2 seconds (no OCR needed, tiny document, models
> already warm). You can see all of this yourself: `GET /api/v1/documents/{id}/intelligence` and
> `GET /api/v1/documents/{id}/chunks`, or the **Document Intelligence** page in the Streamlit UI.

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

## 6. What Each Chat Message Actually Costs

Every chat message makes up to **8 OpenAI API calls** across Stages A, B, E, and G. Prices below
use OpenAI's published per-1M-token rates: gpt-4o-mini = $0.15 in / $0.60 out, gpt-4o = $2.50 in /
$10.00 out, text-embedding-3-large = $0.13 (flat).

| Stage | Calls | Model | Typical tokens | Cost |
|---|---|---|---|---|
| A — Rewrite, expand, classify, filter | 4 | gpt-4o-mini | ~360 in / 180 out | ~$0.0002 |
| B — Embed 4 query variants | 1 (batched) | text-embedding-3-large | ~60 | ~$0.00001 |
| D — Rerank | 0 (passthrough by default) | — | — | $0.00 |
| E — Compress up to 10 retrieved chunks | up to 10 (parallel) | gpt-4o-mini | ~3,160 in / 800 out | ~$0.0010 |
| G — Generate the answer | 1 (streamed) | **gpt-4o** | ~1,400 in / 300 out | **~$0.0065** |
| **Total (typical message)** | ~7-8 calls | | | **~$0.0076 (≈0.76¢)** |
| Total (worst case: context maxed at `CONTEXT_MAX_TOKENS`, full `ANSWER_MAX_TOKENS` output) | | | | ~$0.027 (≈2.7¢) |
| **Semantic cache hit** (similarity > `SEMANTIC_CACHE_SCORE_THRESHOLD`, default 0.95) | 1 | text-embedding-3-large | ~15 | **~$0.000002** — Stages A, D, E, G all skipped entirely |

**The answer-generation call (Stage G, gpt-4o) is ~85% of the cost of a typical message** — it's
the only stage using the large, expensive model; everything else runs on gpt-4o-mini (≈17x
cheaper per token) or a free local step. If you're optimizing cost, that's the one call to focus
on: shrinking `CONTEXT_MAX_TOKENS`, tightening `RERANK_TOP_N` (fewer chunks → less context to
compress and send), or switching `LARGE_LLM_PROVIDER` to a cheaper model all reduce it directly.
The semantic cache is the single biggest lever, though — a cache hit is ~4,000x cheaper than a
full pipeline run, which is exactly why it exists.

---

## 7. What Each Document Upload Actually Costs

Ingestion only spends OpenAI money in two places, and **this hasn't changed since Phase 4A** —
OCR (Tesseract) and chunking-role embeddings (BGE-M3) both default to free local models, so the
new Document Intelligence steps add $0.00 by design:

| Step | Model | Cost driver |
|---|---|---|
| Metadata enrichment (Station 2) | gpt-4o-mini | fixed — only the first 2000 chars, once per document |
| Chunk embedding, retrieval role (Station 4) | text-embedding-3-large | scales with how much text survives chunking |

| Document | Enrichment | Embedding | **Total** |
|---|---|---|---|
| Our worked example (5 chunks, 1 embedded) | ~$0.0002 | ~$0.00003 | **~$0.0002** |
| A ~20-page report (~40 embedded chunks) | ~$0.0002 | ~$0.0013 | **~$0.0015** |
| **Real 339-page book actually indexed in this project** (564 embedded chunks, 126,270 tokens — pulled straight from Postgres) | ~$0.0002 | **$0.0164** | **~$0.0166** |

**If you ever set `CHUNKING_EMBEDDING_PROVIDER=openai`** (instead of the `bge_m3` default):
`SemanticChunker` embeds every *sentence* inside every structural section, not just the final
chunks — a much finer granularity. For that same 339-page book, this roughly **doubles** ingestion
cost to ~$0.03. Not the current configuration; just the lever to know about if you ever flip it.

**OCR itself never touches OpenAI at all** — `TesseractProvider` (the default) runs entirely on
your CPU. The only way OCR would cost API money is switching `OCR_PROVIDER=baidu_unlimited`
(a *separate*, Baidu-billed cost, unrelated to your OpenAI key) — and that provider is currently
an untested stub with no wired credentials, not something that runs today.

---

## 8. Environment Variables — What Controls What

```ini
# Which LLM provider is active
SMALL_LLM_PROVIDER=openai   # stages A, context compression, enrichment
LARGE_LLM_PROVIDER=openai   # stage G answer generation

# Which models
OPENAI_SMALL_MODEL=gpt-4o-mini
OPENAI_LARGE_MODEL=gpt-4o

# Which reranker (D stage)
RERANKER_PROVIDER=passthrough   # passthrough | bge | cohere

# Which embedding model (retrieval role -- what's stored in Qdrant)
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_MODEL=text-embedding-3-large

# How many results each search returns
VECTOR_SEARCH_TOP_K=20   # Qdrant returns this many
BM25_SEARCH_TOP_K=20     # Elasticsearch returns this many
RERANK_TOP_N=10          # final chunks passed to LLM

# --- Phase 4A: Document Intelligence ---

# OCR (Stage 1b)
OCR_PROVIDER=tesseract          # tesseract | paddle | baidu_unlimited
OCR_MIN_WORDS_PER_PAGE=10.0     # below this ratio, a PDF is treated as scanned

# Chunking-role embedding (Stage 3's SemanticChunker only -- never stored in Qdrant)
CHUNKING_EMBEDDING_PROVIDER=bge_m3   # bge_m3 | e5_large | openai

# Hybrid chunking thresholds
SEMANTIC_CHUNK_STD_MULTIPLIER=1.0   # adaptive threshold = mean - multiplier * stdev
SEMANTIC_CHUNK_MIN_SENTENCES=3      # sections shorter than this skip semantic splitting
CHUNK_VALIDATOR_MIN_CHARS=10
CHUNK_VALIDATOR_MIN_OCR_CONFIDENCE=0.35
```

---

## 9. Summary — One Line Per Component

| Component | File | Does what |
|---|---|---|
| DoclingLoader | `src/ingestion/loaders/` | PDF → raw text (+ structural labels since Phase 4A) |
| ImagePassthroughLoader | `src/ingestion/loaders/image_loader.py` | Images → empty text (OCR fills it in) |
| OCRDetector / OCRProvider | `src/ingestion/ocr/` | Skip-vs-OCR decision + Tesseract/Paddle/Baidu recognition |
| LayoutAnalyzer | `src/ingestion/layout/` | Headings/tables/figures/lists/forms/footnotes/outline |
| DocumentParsingService | `src/ingestion/parsing/` | Unifies Load+OCR+Layout into one `ParsedDocument` |
| LLMMetadataEnricher | `src/ingestion/enrichers/` | Tag document with GPT-4o-mini |
| HybridChunkingPipeline | `src/ingestion/chunkers/hybrid_chunking_pipeline.py` | Structure → Semantic → Parent/Child → Validate |
| ParentChildChunker | `src/ingestion/chunkers/` | Two-level token windowing, reused inside HybridChunkingPipeline |
| EmbeddingStrategy | `src/ingestion/embedders/` | Dual-role embeddings: chunking (BGE/E5) vs retrieval (OpenAI) |
| DocumentIntelligenceRepository | `src/domain/repositories/` + `src/infrastructure/database/postgres/` | Persists OCR/layout/embedding summary per document |
| QueryAgent | `src/retrieval/agents/` | Rewrite + expand + classify query with LLM |
| HybridRetriever | `src/retrieval/hybrid_retriever.py` | Search Qdrant + Elasticsearch in parallel |
| Fuser (RRF) | `src/retrieval/fusers/` | Merge two result lists by rank, not score |
| Reranker | `src/retrieval/rerankers/` | Pick top 10 (passthrough = by RRF, bge = by AI) |
| ContextProcessor | `src/retrieval/context/` | Compress + deduplicate + fit in token budget |
| AnswerPipeline | `src/retrieval/answer/` | Stream answer word-by-word from GPT-4o |
| SemanticCache | `src/retrieval/cache/` | Skip pipeline if same question asked before |
| EvaluationRunner | `src/evaluation/offline/` | Score answers on faithfulness/relevancy |
| benchmark_chunking.py | `scripts/` | A/B compares HybridChunkingPipeline vs the pre-Phase-4A chunker |

---

## 10. Troubleshooting — Things We Actually Hit Running This

Four real, reproduced issues from getting Phase 4A running end-to-end, each with root cause and
fix — kept here in plain language so the next person hitting the same symptom finds the answer
immediately instead of re-debugging from scratch. Full technical write-ups are in
`docs/architecture/12_phase4a_design_review.md`.

### "My document has been stuck on 'processing' forever"

**Most likely cause (fixed):** a worker process crashed or froze partway through, silently —
no error ever reached the document's status because the background task died with it. Check
`docker logs <api-container> --tail 50` for either:
- `died` / `Started server process` lines with no matching error just before them → a worker was
  killed (was: OOM from 2 workers each loading a full copy of every ML model — fixed by dropping
  to `--workers 1` in `docker/api.Dockerfile`), **or**
- total silence, no new log lines at all for the whole time it's been "processing" → the event
  loop was blocked (was: `DoclingLoader`/`UnstructuredLoader` ran their CPU-heavy parsing directly
  instead of via `asyncio.to_thread`, freezing the *entire* API, not just that upload — fixed in
  both loaders).

**How to tell it's actually fixed for you:** upload a PDF, then immediately fire a few
`curl http://localhost:8000/api/v1/health` calls while it's still "processing." They should all
return `200` in well under a second. If they hang too, the API itself is frozen — that's the bug
above; if only the upload is slow but health checks stay fast, it's just a genuinely large/complex
document taking its time (normal).

**If a document is stuck and unrecoverable:** `DELETE /api/v1/documents/{id}` and re-upload — the
background task that died can't be resumed, but nothing else is corrupted by deleting and retrying.

### "OCR says it was skipped but my PDF has scanned pages"

Not necessarily a bug — see Station 1b's worked example above. Docling runs its **own** internal
OCR engine automatically on anything it classifies as scanned/image content, before `OCRDetector`
ever looks at the result. `ocr_ran: false` in `GET /documents/{id}/intelligence` means "the
*supplemental* Tesseract/Paddle/Baidu layer wasn't needed," not "no OCR ever ran on this document
at all." If you suspect real text loss, check `GET /documents/{id}/chunks` for the specific page —
if it has a reasonable number of chunks with real content, nothing was lost.

If a *specific page* genuinely has no extractable text (confirmed via the chunks endpoint), and
OCR still shows skipped, check `OCR_MIN_WORDS_PER_PAGE` (default 10) — a page just above that
threshold with garbled/sparse real content might need a higher value for your document types.

### "I uploaded an image (.png/.jpg) and got a 422 error, or OCR never actually ran"

Three separate, real bugs stacked here originally — if you're on an image built before this was
fixed, you may still hit one:

1. **`422 File type 'png' is not supported`** — `ALLOWED_FILE_TYPES` in `.env`/`.env.example` needs
   to include `png,jpg,jpeg,tiff,bmp` alongside the document types. Check it's actually set, then
   restart the container (env changes need a restart — see the next entry below).
2. **Upload succeeds but `ocr_ran` never becomes `true`, or ingestion fails outright** — the
   `tesseract` binary and poppler (`pdftoppm`) need to actually be installed in the API container
   (`tesseract-ocr` + `poppler-utils` in `docker/api.Dockerfile`'s apt-get list). Check with
   `docker exec <api-container> which tesseract` — if that returns nothing, rebuild the image.
3. **Same symptom as #2 even with the binary installed** — the Python OCR library installs under
   the module name `unstructured_pytesseract`, not `pytesseract` (a naming quirk of the
   `unstructured[all-docs]` dependency that provides it) — `src/ingestion/ocr/registry.py` needs to
   import it under that name.

**How to confirm it's actually fixed:** upload any plain image with visible text and check
`GET /documents/{id}/intelligence` — you should see `ocr_engine: "tesseract"`, `ocr_ran: true`, and
a real `ocr_confidence_avg` (not `null`). This is genuinely the only reliable way to confirm the
supplemental OCR layer works at all, since every PDF test tends to route around it via Docling's
own internal OCR (see Station 1b's worked examples above) — a bug here can sit silently unnoticed
indefinitely otherwise.

### "I changed a chunking/embedding setting and nothing happened"

`IngestionPipeline` is built once and cached for the life of the API process (see
`get_ingestion_pipeline()` in `src/api/dependencies.py`) — config changes in `.env` need a
container restart (`docker compose restart api`) to take effect, not just a file save.

### "Uploaded files disappear after a container restart"

Expected, not a bug (yet) — `./uploads/` is not a mounted volume in `docker-compose.yml`, so it's
part of the container's writable layer and gets wiped on `docker compose up`/rebuild. The
*ingested* data (Postgres rows, Qdrant vectors, Elasticsearch documents) all persist fine — only
the original raw uploaded file is at risk, and only until the next container recreation. If you
need the original file to be re-processable later (e.g. to re-run ingestion with different
settings), keep a copy outside the container until `uploads/` is made a persistent volume.
