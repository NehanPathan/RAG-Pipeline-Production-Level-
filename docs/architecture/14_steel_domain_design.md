# 14. Steel Structure Design — Domain Extension Design

**Status:** approved, in build
**Owner:** platform-team
**Base:** working-tree copy of `RAG-Pipeline-Production-Level-` (baseline commit `d8e12a8`). That repository is frozen; all work happens here.
**Extends:** `12_phase4a_design_review.md`, `13_query_routing_and_tools.md`

---

## 14.1 Why this document exists

A client solution-design document specifies an intelligent document processing and contextual search system for **steel structure design documents**: ingest PDF / DOCX / images / DWG, extract text, tables, drawings and CAD content, OCR scanned material, recognise domain entities (beam sections, steel grades, load capacities), and expose semantic + keyword + metadata-driven search with access control and versioning.

Most of that already exists in this codebase. This document records what is reused, what is genuinely missing, and the design of the additions — with the access model stated in one place so a reviewer does not have to reconstruct it from five files.

## 14.2 What the requirement asks for vs what already exists

| Requirement | Existing implementation |
|---|---|
| Ingest PDF, DOCX, images | `DoclingLoader`, `UnstructuredLoader`, `ImagePassthroughLoader` — `src/ingestion/loaders/` |
| OCR scanned content | `OCRDetector` (per-page word-density) + pluggable `OCRProvider` registry — `src/ingestion/ocr/` |
| Table extraction | Docling grid extraction → `TableBlock` |
| Text parsing / segmentation | `HybridChunkingPipeline`: structure → semantic → parent/child → validator |
| Embeddings | Dual-role `EmbeddingStrategy` (bge-m3 chunking, text-embedding-3-large retrieval), Redis-cached |
| Vector / semantic search | Qdrant with payload indexes |
| Keyword / filtered search | Elasticsearch 8.16 BM25, fused with vectors via RRF |
| Metadata store | Postgres 16, SQLAlchemy 2 async, Alembic |
| RAG chatbot layer | `QueryPipeline` modules A–G with validated `[n]` citations, SSE streamed |
| Access controls | Firebase auth, RBAC, 4-level clearance (pre-filter **and** post-filter), append-only audit log |
| Search UI | Streamlit, 7 pages including a retrieval inspector |

**Not present, and required:**

1. **CAD ingestion** — no DWG or DXF support of any kind.
2. **Domain entity recognition** — the enricher is generic (`person|organization|date|product|location`); it knows nothing about `ISMB 300` or `Fy = 250 MPa`.
3. **Coordinate provenance** — `TextBlock.bbox` exists and is populated, but dies at `StructureChunker`; there is no way to highlight a matched region.
4. **Object storage** — originals are written to a local `./uploads` directory that is not even mounted as a docker volume.
5. **Versioning** — no revision model at all, and for steel drawings Rev A/B/C *is* the domain.
6. **Job queue** — ingestion runs in in-process `BackgroundTasks`; CAD and vision work is minutes-long.
7. **Project/job entity** — isolation is per-`user_id`; steel work is organised by project and shared between engineers.

## 14.3 Decisions

| Area | Decision | Rationale |
|---|---|---|
| **DXF** | `ezdxf` (MIT) | Pure-Python core, cp312 wheels for Windows and manylinux, reads TEXT/MTEXT/ATTRIB/DIMENSION/INSERT, layers, blocks. |
| **DWG** | ODA File Converter, **operator-installed** | ODA's EULA grants free use but **not redistribution**, so the binary cannot be baked into an image we publish. Absent converter ⇒ actionable error, not a crash. LibreDWG rejected (GPL-3.0 conveyance obligations for on-prem images); Autodesk Platform Services rejected by default (uploads client drawings to a third party). |
| **Drawing vision** | Classical CV + region-targeted OCR now; VLM on crops later; bespoke detector only after labels accrue | Detectron2 has no PyPI wheels and breaks `uv sync --locked`. `ultralytics` YOLO is AGPL-3.0. A custom detector needs 500–2000 labeled drawings that do not exist yet. |
| **Domain NER** | Regex + YAML gazetteer first, LLM second, merged with provenance | Deterministic, auditable, and ~40 patterns beat a 200 MB spaCy install for this vocabulary. Regex always outranks the LLM on a tie. |
| **Blob storage** | S3-compatible: MinIO in compose, real S3 in cloud, one `BlobStore` port | Portable, and lets a client self-host if drawings cannot leave their network. |
| **Asset serving** | Proxy bytes behind our own short-lived asset tokens | Presigned URLs bypass clearance and project checks entirely. That is the one thing this system must not do. |
| **Page rendering** | `pypdfium2` (Apache-2.0/BSD-3) | PyMuPDF is AGPL-3.0. |
| **Job queue** | `arq` (MIT) | Async-native, reuses the Redis we already run, no extra broker or result backend. Celery is prefork/thread-oriented; RQ is sync-only. |
| **Frontend** | Streamlit first, Next.js after | De-risks the new APIs before a React app is written against them. |
| **Framework** | LangChain + LangGraph, LangSmith for evaluation only | See 14.3.1. |

### 14.3.1 LangChain / LangGraph adoption

Adopted where a maintained implementation exists and delegating loses nothing:

| Now | Was |
|---|---|
| `ChatOpenAI` / `ChatAnthropic` / `AzureChatOpenAI` | Three hand-written provider clients (~400 lines of HTTP plumbing, streaming-chunk parsing, usage extraction, per-vendor quirks) |
| `OpenAIEmbeddings` / `HuggingFaceEmbeddings` | An AsyncOpenAI wrapper with its own batching and retry, plus two SentenceTransformer wrappers |
| **LangGraph `StateGraph`** (`src/retrieval/graph/`) | Five async generators delegating into one another with early returns at eight points, plus a separate hand-rolled node-graph workflow engine (deleted) |

**Not delegated, and why.** Each of these would lose behaviour the framework has no equivalent for:

- **`LLMGateway`** stays wrapped around the chat models. It holds the approved-provider policy check, per-provider Prometheus metrics, cost accounting, feature-flag-gated fallback, and the rule that a stream never fails over once a token has reached the client. `.with_fallbacks()` covers only the failover, and silently.
- **The embedding cache.** LangChain's `CacheBackedEmbeddings` is `langchain-classic` in v1 and reports no hit rate. That rate is a direct cost control — every miss is a billed call.
- **The E5 prefix convention.** `HuggingFaceEmbeddings` has no equivalent, and omitting `query: ` / `passage: ` degrades retrieval silently rather than erroring.
- **The clearance pre-filter and the governance layer.** No LangChain equivalent exists, and this is the security boundary.
- **The semantic cache's numeric-literal guard**, which stops "within 30 days" matching a cached "within 14 days" answer — dangerous for load capacities and dimensions.
- **The evaluation judges.** They return `None` on an unparseable response and exclude it from aggregation, rather than scoring 0.5 and making it indistinguishable from a mediocre answer.

**A note on LangChain 1.x.** This resolved to `langchain-core` 1.5 / `langgraph` 1.2, where the `langchain` package is essentially empty: `EnsembleRetriever`, `ContextualCompressionRetriever`, `CrossEncoderReranker` and `CacheBackedEmbeddings` all moved to `langchain-classic`. New work is not built on a package that ships as "classic", so retrieval composition is LangGraph nodes over `langchain-core` abstractions, and the small RRF fusion and reranking helpers stay as they are.

**LangSmith is evaluation-only.** LangChain enables LangSmith export from environment variables alone, with nothing in the code opting in — which for this system would send prompt text and retrieved drawing content off-network as a side effect of a key set to run an eval. `src/monitoring/langsmith.py` therefore sets the flags explicitly in both directions at startup, and logs a warning when export is on. Runtime tracing stays on Langfuse, which is self-hostable.

## 14.4 Access model (single source of truth)

Three independent dimensions. **All three must pass.** None replaces another.

1. **Role** (`src/governance/rbac.py`) — `viewer | analyst | steward | admin`, read from the database, never from a client value. `require_role` is an allow-list, not a rank comparison.
2. **Data clearance** (`src/domain/value_objects/sensitivity.py`) — `public < internal < confidential < restricted`. Enforced twice: pushed into the Qdrant/ES query as a pre-filter, and re-checked post-retrieval by `sensitivity_guard`, where any drop is treated as a defect (counter + critical alert).
3. **Access scope** (new) — a document is readable if `documents.user_id == principal.user_id` **OR** `documents.project_id ∈ principal's project memberships`.

Dimension 3 changes the tenant filter from **equality** to a **disjunction** in exactly three places, and it must be done once, deliberately, in all three:

- `src/domain/value_objects/metadata_filter.py`
- `src/infrastructure/vector_store/qdrant/repository.py`
- `src/infrastructure/search/elasticsearch/repository.py`

The disjunction lives inside the *security* filter that `MetadataFilterSpec.security_only()` preserves on the empty-result retry, so a widened retry can never relax it. A project member still cannot read a `restricted` document above their clearance — project membership grants **reach**, clearance grants **depth**.

Documents with `project_id IS NULL` are personal: visible only to their owner. Existing rows migrate as NULL, so the migration cannot accidentally widen access.

Unreadable documents return **404, not 403**, matching the existing convention (`_load_readable_document`), so the API does not confirm the existence of a document the caller may not know about.

## 14.5 Revision model

A `drawings` row is the stable identity (drawing number + sheet). N `documents` rows are its revisions.

```
drawings (id, drawing_number, sheet_number, project_id, discipline, title)
   └── documents (…, drawing_id, revision_label, revision_index, revision_date,
                  revision_note, is_latest, superseded_by_document_id, superseded_at)
```

- A partial unique index (`uq_drawings_one_latest`) enforces exactly one `is_latest` per drawing.
- `document_chunks` denormalizes `drawing_id`, `drawing_number`, `revision_label`, `revision_index`, `is_latest` so retrieval filters without a join.
- Search defaults to `is_latest = true` (`search_default_latest_only`), with an explicit "include superseded revisions" toggle.
- Registering a revision flips `is_latest` in one transaction, partial-updates both search backends, then **invalidates the semantic cache for the whole revision family** — not just the new document. Otherwise a cached answer keeps quoting Rev B after Rev C lands.

## 14.6 Ingestion additions

### CAD → the existing `RawDocument` IR

No parallel pipeline. `DxfLoader` maps CAD constructs onto the intermediate representation the rest of the system already speaks:

| CAD construct | IR target |
|---|---|
| Paper-space layout (sheet) | one **page**; modelspace is page 1 |
| TEXT / MTEXT / ATTRIB | `TextBlock(element_label="cad_text"…, section=layer, bbox=…)` |
| Layer | `TextBlock.section` → one structural section per layer. A steel drawing's layers *are* its semantics (`S-BEAM`, `S-DIM`, `TITLE`). |
| Title-block ATTRIBs | structured `TitleBlockField` records **and** a synthesized `title_block` text block, so the facts are both queryable and retrievable as prose |
| DIMENSION | `DimensionRecord` → per-sheet dimension schedule, with **exact** numerics (no OCR error) |
| INSERT (block reference) | `PartInstance` → BOM-like table aggregated by block name and piece mark |
| Rendered sheet | `RawDocument.page_images` |

**Coordinate space.** `TextBlock.bbox` is stored in **normalized render space** (0..1, Y-down, relative to that layout's raster) so one highlight renderer serves PDFs, scans and CAD identically. Raw model-space rects (Y-up, unbounded, possibly negative) stay on the CAD-native records. `BoundingBox` gains `space: "page" | "image" | "model"`.

**Render always; vision only when degenerate.** Every layout renders to PNG because preview and highlighting need it anyway and it costs no LLM tokens. The scanned-drawing path is entered only when the DXF is degenerate — too few text entities, a high proxy-entity ratio, or all text trapped in unexploded anonymous blocks — i.e. the drawing was exported as pure geometry and the words are literally polylines.

### Drawings that arrive as PDFs

Most steel drawings are not DXF. They are plotted to PDF, or scanned from a plan chest, and the pipeline has to recognise all three cases without a second RAG stack. `DrawingContentDetector` (`src/ingestion/parsing/drawing_detector.py`) classifies a loaded document and hangs the result on `ParsedDocument.classification` — deliberately **not** on `RawDocument`, which is the loaders' output contract and cannot answer this: the question is only decidable once the file and the extracted text can be compared.

Measured on the reference fixtures:

| | native text layer | words/page | periods per 100 chars |
|---|---|---|---|
| Vector CAD PDF | 60 words | 60 | 0.34 |
| Scanned drawing PDF | **0 words** | 60 | 0.35 |
| Prose specification | 219 words | 219 | 1.83 |

Two orthogonal signals, each answering a different question:

- **Is there a native text layer?** (pypdf, reading the file directly.) This is the *only* reliable scan-vs-plot discriminator, because **Docling silently OCRs scanned pages with its internal RapidOCR** and returns the result as ordinary extracted text — same word count, same block count, same labels. By the time the pipeline sees a `RawDocument` the two are indistinguishable.
- **Does the text read like sentences?** Prose terminates sentences; a drawing is labels, dimensions and schedule rows. Title-block markers (`DRAWING NO`, `SCALE 1:100`, `REV`) override both statistics when present, since prose does not say those things in passing. The two statistical signals must *agree* — a short cover note trips words-per-page alone, a table of contents trips sentence-density alone, and neither is a drawing.

`0` and `None` are different answers from the probe: "I read the file and there is no text" versus "I could not read the file". Only the first is evidence of a scan. Inferring the first from a missing or corrupt file would turn an operational error into a forced full-page OCR pass on every document.

**What the classification routes:**

- A **scanned** PDF is sent to our own OCR even though it looks text-rich, because the text it appears to have carries no confidence score and no word coordinates — the two things `ChunkValidator` and region highlighting are built on. `OCRDetector.detect(..., has_native_text_layer=False)`; switchable off via `ocr_pdfs_without_text_layer` where the extra pass is not worth its cost.
- A **drawing** is validated against the drawing OCR confidence floor (0.15) rather than the prose floor (0.35). This previously keyed off the *loader name*, so only an uploaded image ever qualified and a drawing exported to PDF — which is how drawings actually arrive — was held to the prose floor. That is the same failure as the bug the floor was written to fix: the drawings most in need of it were the ones that never got it.
- A **scanned prose** report keeps the standard floor. It is laid out as prose and should OCR cleanly, so a low score there is a real problem, not the nature of the input.

OCR failure is non-fatal when the loader already extracted usable text. Routing scanned PDFs to OCR made that path reachable for documents that previously ingested fine, and a host without poppler or tesseract must not turn this fix into a regression: the confidence scores and geometry are lost, the document is not. With no text to fall back on it still raises, because silently indexing an empty document is worse than reporting the failure.

**A PDF drawing is not a DXF, and the difference is queryable.** `ContentKind.has_exact_dimensions` is true only for `cad_native`. A dimension read from a DXF is the CAD value; on a plotted sheet it is the string the drafter's CAD system rendered; on a scan it is OCR's reading of that string. `content_kind` is denormalized onto every chunk, indexed as a keyword in both backends, exposed as a search facet and filter, and given a Postgres column in migration `0007` — without a system of record there, the first run of `scripts/reindex_chunks.py` would erase it from both stores, exactly the trap 14.8 records for the other denormalized fields.

### Steel entity extraction

`src/ingestion/extractors/` — hybrid, in strict precedence order:

1. **Regex + gazetteer** (`config/steel_sections.yaml`): section designations (ISMB/ISMC/ISLB/ISWB/ISHB/ISA, IPE/HEA/HEB/UPN, UB/UC/PFC, W/HP/S/MC/L/WT, HSS/SHS/RHS/CHS), grades (`Fe 415`, `S355 J2`, `A36`, `IS 2062 E250`, `Fy = 250 MPa`), bolts (`M20 8.8`, HSFG), welds (fillet/CJP/PJP sizes, electrode class), dimensions with SI normalisation, load capacities (context-gated), and drawing/part/mark/revision/project numbers (**gated by a preceding key** — the gate is what stops it consuming every alphanumeric token on a drawing). Confidence 0.95 gazetteer-validated / 0.85 pattern-only / 0.6 context-gated.
2. **Normalizer** — `ISMB300`, `ISMB 300` and `I.S.M.B.-300` canonicalize to one string. This is what makes them retrieve the same chunk.
3. **LLM extractor** — only for what regex is bad at (member descriptions in prose, connection descriptions, design assumptions). Confidence clamped ≤ 0.75 so regex always wins a tie.
4. **Merger** — union occurrences so provenance is never lost. **Hallucination guard:** an LLM section designation that is neither in the gazetteer nor a substring of the document text is dropped and logged.

Entities are written to the **already-existing** `document_metadata.custom_metadata` JSONB, and canonical designations are appended to `metadata.tags` — which is **already indexed as a keyword field in both Qdrant and Elasticsearch**. That gives a filterable steel-entity index for zero new index fields.

### Chunking by content type

`ChunkingStrategy` is already a Protocol, so routing is clean. Drawings and schedules are not prose: they have almost no `.`-terminated sentences, so `SemanticChunker`'s sentence regex returns one giant "sentence" and the whole semantic stage silently degrades to a no-op. `DrawingChunkingPipeline` therefore skips it entirely and emits one chunk per semantic unit — title-block facts, revision-table row windows, BOM row windows, notes blocks, per-layer text, and a **sheet summary chunk** per page (drawing number + title + all part marks) that answers "which drawing shows the base plate detail".

Tables keep their cell grid in `table_data` and split into **row windows with the header repeated**, each prefixed `Table: {caption} (rows N–M of T)` so a window is self-describing when retrieved out of context.

## 14.7 Provenance and highlighting

A chunk spans many blocks, so it carries a **list of regions**, not one rectangle:

```python
@dataclass(frozen=True)
class Region:
    page_number: int
    x0: float; y0: float; x1: float; y1: float
    space: str = "page"     # "page" | "image" | "model"
```

`ChunkMetadata` gains `regions` and `region_precision` (`"block" | "section" | "page"`). The semantic chunker has no sentence→bbox map, so it propagates its parent section's regions and **labels the precision honestly** — the UI draws a soft highlight for section precision and a tight box for block precision, rather than drawing a confident rectangle around the wrong sentence.

Regions are stored but **not indexed**: a JSONB column in Postgres, an unindexed payload key in Qdrant, and `{"type": "object", "enabled": false}` in the Elasticsearch mapping — otherwise every document indexes thousands of float subfields for a field nothing ever filters on.

## 14.8 Known traps this design must not re-enter

Recorded because each one silently produced wrong behaviour and left no error:

- **`create_index_if_not_exists` short-circuits on an existing index.** A changed `INDEX_MAPPINGS` is silently ignored on any existing deployment. Every mapping change must go through `ensure_mapping()` at startup.
- **Qdrant payload indexes must be added in two places** — collection creation *and* `ensure_payload_indexes` — or existing deployments full-scan the new field forever.
- **The denormalized chunk fields had no system of record.** `user_id`, `domain`, `tags`, `file_type`, `document_name` lived only in Qdrant and Elasticsearch, so any path that reloaded chunks from Postgres and re-indexed destroyed them. Fixed by giving them Postgres columns and by using partial-update ports instead of whole-document rewrites.
- **Two hand-built payload dicts drift.** Qdrant and Elasticsearch now share one `chunk_to_payload` / `payload_to_chunk` pair, so every new field lands in both stores by construction.
- **Deciding what a document *is* from its loader name.** `_is_drawing` keyed off `loader_name == "image_passthrough"`, so a drawing exported to PDF loaded through Docling like any report and never received the drawing OCR floor. Content classification is a property of the content, not of which loader happened to read it.
- **A downstream library's silent OCR erases the evidence you need.** Docling OCRs scanned pages internally and returns the text as if it had been extracted, so word-density checks see a healthy page and skip our OCR — losing the confidence and coordinates that the text never had. The file itself has to be probed before any of that is merged in.
- **A document-level average used as a per-chunk threshold rejects everything.** `ChunkValidator` compared each chunk against the *document's* mean OCR confidence; scanned drawings average 0.30–0.50 and lost every chunk while still reporting `status=indexed`. Confidence is now per-chunk, with a separate floor for drawings.
