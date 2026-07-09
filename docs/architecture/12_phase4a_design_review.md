# Phase 4A Design Review — Production Document Intelligence Upgrade

> Note: an unrelated "Phase 4" already exists in `10_build_roadmap.md` (multi-provider LLM +
> conversation persistence). "Phase 4A" is a separate track — the document intelligence upgrade
> to the *ingestion* pipeline — not a continuation of that roadmap item.

## 1. Scope

The ingestion pipeline (`src/ingestion/`) currently does:

```
Load → Enrich → Chunk (parent/child) → Embed → Store (Postgres + Qdrant + Elasticsearch)
```

built during Phases 1–3 and documented in `docs/HOW_IT_WORKS.md` and `11_phase3_design_review.md`.
It has no OCR (scanned PDFs/images silently produce near-empty text), no layout awareness
(Docling's own heading/list/table/footnote labels are extracted internally then thrown away,
flattened into plain `TextBlock(text, page_number)`), a single fixed-size chunking strategy, and
one hard-coded embedding model shared between chunking and retrieval.

Phase 4A upgrades this to:

```
Load → Detect+OCR → Layout → Enrich → Hybrid Chunk → Embed (dual-role) → Store → Intelligence Record
```

This document is the design review requested before implementation: where each new module fits
into the existing layering (domain entities/repositories, infrastructure adapters, `ingestion`/
`retrieval` as self-contained vertical modules with their own ports+adapters, `api/dependencies.py`
as the manual composition root), and how it integrates without rewriting Phase 1–3 code.
**No implementation code ships with this document** — it defines the build order that subsequent
turns execute module-by-module, each appending an entry to §7 (Module Log), exactly like
`11_phase3_design_review.md` did.

---

## 2. What Already Exists (Reusable As-Is)

| Layer | Asset | Reuse in Phase 4A |
|---|---|---|
| `ingestion/loaders/base.py` | `DocumentLoader` ABC, `RawDocument`, `TextBlock`, `TableBlock`, `ImageRef` | `RawDocument` becomes the input the new parsing stage augments; loader ABC signature (`load(file_path) -> RawDocument`) unchanged |
| `ingestion/loaders/{docling,unstructured}_loader.py` | Existing loaders, `_select_loader()` in `pipeline.py` | Unchanged behavior for text-native files (docx/md/html/txt skip OCR entirely); gain additive label population (see Gap 1) |
| `ingestion/chunkers/parent_child_chunker.py` | `ParentChildChunker`, `ChunkingConfig`, token-window splitting logic | Reused **as a pipeline stage**, not replaced — called per-section by the new `HybridChunkingPipeline` instead of once on the whole document |
| `ingestion/embedders/base.py` | `EmbeddingProvider` ABC (`embed_texts`, `embed_query`, `model_id`, `dimensions`) | Reused unchanged as the contract for BGE-M3/E5 providers — already generic enough, no interface change needed |
| `ingestion/embedders/openai_embedder.py` | Retry (`tenacity`) + Redis-caching pattern (`_cache_key`/`_get_cached`/`_set_cached`) | Template for the retry/caching shape of new local providers |
| `retrieval/rerankers/registry.py`, `llm/registry.py` | `get_reranker(settings) -> Reranker`, `get_llm_provider(settings, role) -> LLMProvider` factory pattern | Exact template for `get_ocr_provider(settings) -> OCRProvider` and `get_embedding_provider(settings, role) -> EmbeddingProvider` |
| `domain/entities/document.py` | `Document`, `DocumentMetadata`, `DocumentChunk`, `ChunkMetadata`, `ChunkType` | Extended additively (new optional fields only) — see §4 |
| `domain/repositories/document_repository.py` | `ChunkRepository` ABC | Unchanged; a new `DocumentIntelligenceRepository` ABC is added alongside it, not merged into it |
| `infrastructure/database/postgres/models.py` + Alembic migrations | SQLAlchemy models | New columns on `DocumentChunkModel` + one new table, via additive migration |
| `ingestion/pipeline.py` | `IngestionPipeline.ingest()` 8-step orchestration, `_select_loader()` | Gets new steps inserted (OCR detection → OCR → layout → hybrid chunk); same try/except/status-tracking shape kept |
| `api/dependencies.py` | `_build_ingestion_pipeline()` singleton-factory pattern | Same pattern extended with new sub-factories (`_get_ocr_provider()`, `_get_layout_analyzer()`, `_build_embedding_strategy()`) |
| `evaluation/offline/{metrics,runner,dataset}.py` | `score_faithfulness`/`score_answer_relevancy`/`score_context_relevancy`/`score_answer_correctness`, `run_evaluation()`, `EvalDataset` | Reused directly by the Part 8 benchmark script — no new scoring logic invented |
| `pyproject.toml` deps | `sentence-transformers`, `torch` already pinned | BGE-M3/E5-Large local embedding + Tesseract need only `pytesseract` (+ system binary) added; PaddleOCR/Baidu deps added but unused until later (see Gap 2/3) |
| `docs/architecture/11_phase3_design_review.md` | Structure/format template | This review follows the same section layout + Module Log convention |

---

## 3. Gap Analysis & Key Decisions

### Gap 1 — Layout fidelity requires touching the loaders (resolved)

Docling's `doc.texts` items already carry a `.label` (`section_header`, `list_item`, `caption`,
`footnote`, `title`, `page_header`/`footer`, `text`) and heading level; Unstructured's `element`
objects carry an equivalent `.category`. `DoclingLoader`/`UnstructuredLoader` currently discard
this, flattening everything into `TextBlock(text, page_number)`. Part 3 (headings/tables/figures/
captions/lists/forms/footnotes/outline) is not achievable at real fidelity without this data.

**Decision:** extend `RawDocument`/`TextBlock` with new *optional* fields (`element_label`,
`heading_level`, `is_footnote`, `bbox`; plus `RawDocument.forms`/`outline`), defaulted so existing
construction call sites are untouched, and populate them inside `DoclingLoader`/`UnstructuredLoader`
with additive lines only — no existing field, method signature, or behavior changes. Verified by
`tests/unit/ingestion/` staying green with zero edits once implemented. The alternative (a
zero-touch heuristic analyzer inferring structure from flattened text) was rejected — materially
worse accuracy, especially for forms/footnotes which have no signal left once flattened.

### Gap 2 — OCR engine footprint vs. this environment (resolved)

Tesseract is a lightweight system binary + thin Python wrapper (`pytesseract`), no GPU needed.
PaddleOCR pulls in `paddlepaddle`, a full deep-learning framework that is heavy on CPU-only hosts —
and this environment is confirmed CPU-only (the BGE reranker previously timed out at 115s on CPU
and was swapped to passthrough, per `docs/HOW_IT_WORKS.md` §Stage D).

**Decision:** `TesseractProvider` ships as a real, working `OCRProvider` now. `PaddleOCRProvider`
is written against the same interface and registered in `registry.py` (satisfying the literal Part
1 requirement), but left unwired/untested until GPU is available — implementing both fully now
would mean a slow, heavy CPU-bound install blocking this pass for a provider that can't be
meaningfully exercised in this environment anyway.

### Gap 3 — No existing Baidu Unlimited OCR code or credentials (resolved)

Repo-wide search (`baidu|unlimited|paddle|tesseract|ocr`, case-insensitive) found zero prior
implementation. Part 2 asks to "research the current implementation" — there is none to build on;
this is net-new, and there are no API credentials to test against.

**Decision:** `UnlimitedOCRProvider` ships as a real class implementing `OCRProvider` (HTTP calls
to Baidu's General Text Recognition/Unlimited REST API), but marked untested/not
production-verified. Strengths/weaknesses/hardware/latency/memory are documented from Baidu's
public API docs and labeled as vendor-sourced, not locally measured — this satisfies "design the
abstraction so it can be plugged in later without changing downstream code" from Part 2 without
fabricating benchmark numbers this environment can't produce.

### Gap 4 — `ParentChildChunker`'s public API is document-scoped, but `HybridChunkingPipeline` needs section-scoped chunking

`ParentChildChunker.chunk(document_id, raw_document)` only operates on a whole `RawDocument`.
`HybridChunkingPipeline` needs to run parent/child splitting **per structural section** (Part 4,
Step 3), not once on the full document.

**Resolution:** add a new method to `ParentChildChunker` (e.g. `chunk_section(document_id, text,
page_number, section_title) -> list[DocumentChunk]`) that reuses the existing private
`_split_into_token_chunks` helper already used internally by `chunk()`. This is additive — `chunk()`
itself is untouched, so any existing direct caller (and `test_parent_child_chunker.py`) is
unaffected. `ParentChildOnlyStrategy` (the Part 8 A/B comparison baseline) continues to call the
existing `chunk()` method unchanged.

### Gap 5 — Chunking strategy must be swappable without touching `IngestionPipeline`'s control flow

Part 8 requires benchmarking "Current Chunker vs Hybrid Chunker" against the same eval dataset.
Adding an `if use_hybrid: ... else: ...` branch inside `IngestionPipeline.ingest()` would couple
the orchestrator to both implementations and complicate the benchmark script (which needs to run
two full ingestion passes with different chunking behavior).

**Resolution:** introduce a `ChunkingStrategy` protocol (`chunk(document_id, parsed_document) ->
list[DocumentChunk]`) satisfied by two implementations: `HybridChunkingPipeline` (production) and
`ParentChildOnlyStrategy` (a thin adapter wrapping the existing `ParentChildChunker.chunk()`
unchanged, ignoring layout/semantic input — used only for the A/B benchmark baseline).
`IngestionPipeline`'s constructor takes whichever is injected; `pipeline.py`'s control flow doesn't
know or care which one it got. `scripts/benchmark_chunking.py` (Module 11) constructs two
`IngestionPipeline` instances differing only in this one parameter.

### Gap 6 — Images have no existing loader

Neither `DoclingLoader` nor `UnstructuredLoader` lists image MIME types in their `SUPPORTED_*`
sets — there is currently no ingestion path for a standalone image file at all, yet Part 1 requires
"Image → OCR" as a first-class case.

**Resolution:** add `ImagePassthroughLoader` (new file, `ingestion/loaders/image_loader.py`)
supporting `image/png|jpeg|tiff|...`, returning a `RawDocument` with empty `text_blocks` (there is
no native text to extract) and `page_count=1`. The new `OCRDetector` treats any `RawDocument` from
this loader as always-OCR-required, and the OCR stage becomes the actual source of text for images
— matching how the existing pipeline already treats DoclingLoader/UnstructuredLoader output as the
source of truth for PDFs/DOCX.

### Gap 7 — Where does "unify OCR + Loader output into one `ParsedDocument`" live?

Part 1 requires "one unified `ParsedDocument` object regardless of which provider generated it."
`RawDocument` (loader output) and `OCRResult`/`OCRMetadata` (OCR output) are structurally different
shapes, and Part 3's layout data is a third. Changing `DocumentLoader.load()`'s return type to
`ParsedDocument` directly would force every loader (including future ones) to also own OCR
detection/execution and layout analysis — conflating three separate concerns into one interface.

**Resolution:** `RawDocument` stays the loader's output (unchanged contract). A new
`DocumentParsingService.process(raw_document, file_path) -> ParsedDocument`
(`ingestion/parsing/parsing_orchestrator.py`) sits between Load and Enrich in `pipeline.py`,
running `OCRDetector` → `OCRProvider` (conditionally) → `LayoutAnalyzer`, and wraps the result as
`ParsedDocument(raw, ocr_metadata, layout)`. This is the one object `HybridChunkingPipeline` and
the enricher's text input consume from that point on — loaders, OCR providers, and layout
analyzers each stay single-purpose and independently swappable.

### Gap 8 — New chunk metadata needs new Postgres columns, additively

Part 5's chunk metadata list (`chunk_id`, `parent_chunk_id`, `document_id`, `page_number`,
`section_title`, `heading_level`, `semantic_cluster`, `chunk_type`, OCR confidence, `language`,
`token_count`, `embedding_model`, `source_file`) is mostly already covered by existing
`DocumentChunk`/`ChunkMetadata`/`DocumentChunkModel` fields (`id`, `parent_chunk_id`,
`document_id`, `page_number`, `chunk_type`, `token_count`, `embedding_model`). `document_name`
(already denormalized onto `DocumentChunk` since Phase 3's Module B multi-tenant fix) already
covers "source_file" — no duplicate field needed. Only `section_title`, `heading_level`,
`semantic_cluster`, `ocr_confidence`, `language` are genuinely new.

**Resolution:** additive `ChunkMetadata` dataclass fields (all optional, default `None`) + one
additive Alembic migration adding the 5 nullable columns to `document_chunks`, plus a new
`document_intelligence` table (document-level OCR/layout/semantic-graph summary — see §4). No
existing column, table, or migration is altered.

---

## 4. Data Model Changes

**`ChunkMetadata`** (`domain/entities/document.py`, additive, all optional):
`section_title: str | None`, `heading_level: int | None`, `semantic_cluster: int | None`,
`ocr_confidence: float | None`, `language: str | None`. Existing fields (`page_number`, `section`,
`contains_table`, `table_data`) are unchanged; `section` (free-text) and the new `section_title`
(structured heading text from layout analysis) are intentionally distinct fields.

**Postgres migration (new, additive):**
- `document_chunks` gains 5 nullable columns: `section_title`, `heading_level`, `semantic_cluster`,
  `ocr_confidence`, `language`. No backfill needed — existing rows read as `NULL`.
- New table `document_intelligence`: `document_id` (FK, unique), `ocr_engine`,
  `ocr_confidence_avg`, `ocr_processing_time_ms`, `layout_outline` (JSONB), `semantic_graph`
  (JSONB — chunk-pair similarity edges, feeds the Part 7 UI graph), `embedding_model_chunking`,
  `embedding_model_retrieval`, `created_at`.

---

## 5. Proposed New Abstractions Summary

```
src/ingestion/ocr/                          ← Part 1 & 2
    base.py            OCRProvider ABC — recognize(file_path, raw_document) -> OCRResult
    models.py           OCRResult, OCRPageResult, BoundingBox, OCRMetadata
    detector.py          OCRDetector — decides skip/required
    tesseract_provider.py    TesseractProvider (real, working — Gap 2)
    paddle_provider.py       PaddleOCRProvider (interface-complete, unwired — Gap 2)
    unlimited_provider.py    UnlimitedOCRProvider (Baidu, interface-complete, untested — Gap 3)
    registry.py          get_ocr_provider(settings) -> OCRProvider

src/ingestion/loaders/base.py                ← Part 1 (additive only — Gap 1)
    TextBlock gains: element_label, heading_level, is_footnote, bbox
    RawDocument gains: forms: list[FormField], outline: list[OutlineNode]

src/ingestion/loaders/{docling,unstructured}_loader.py   ← populate the new fields (additive lines)
    New: src/ingestion/loaders/image_loader.py — ImagePassthroughLoader (Gap 6)

src/ingestion/layout/                        ← Part 3
    base.py             LayoutAnalyzer ABC — analyze(raw_document) -> DocumentLayout
    docling_layout_analyzer.py    reads populated element_label/heading_level
    heuristic_layout_analyzer.py  fallback for loaders/paths without labels (e.g. plain .txt)
    models.py            DocumentLayout, Heading, TableRef, FigureRef, Caption,
                          ListBlock, FormField, Footnote, OutlineNode

src/ingestion/parsing/                       ← unifies Load + OCR + Layout — Gap 7
    parsed_document.py   ParsedDocument (raw, ocr_metadata, layout)
    parsing_orchestrator.py   DocumentParsingService.process(raw_document, file_path) -> ParsedDocument

src/ingestion/chunkers/                      ← Part 4 (ParentChildChunker untouched, reused — Gap 4/5)
    structure_chunker.py     StructureChunker — splits ParsedDocument.layout into StructuralSections
    semantic_chunker.py       SemanticChunker — sentence embeddings + adaptive-threshold boundaries
    chunk_validator.py        ChunkValidator — rejects too-small/duplicate/whitespace/OCR-garbage/low-confidence
    hybrid_chunking_pipeline.py   HybridChunkingPipeline (Structure → Semantic → ParentChild → Validate)
    chunking_strategy.py      ChunkingStrategy protocol + ParentChildOnlyStrategy adapter

src/ingestion/embedders/                     ← Part 6 (EmbeddingProvider ABC untouched, reused)
    bge_embedder.py       BGEEmbeddingProvider (BAAI/bge-m3, local, sentence-transformers — already pinned)
    e5_embedder.py        E5EmbeddingProvider (intfloat/e5-large-v2, local)
    registry.py           get_embedding_provider(settings, role="chunking"|"retrieval") -> EmbeddingProvider
    embedding_strategy.py     EmbeddingStrategy(chunking_provider, retrieval_provider) — extension
                          point for future ensemble providers, documented not built now

src/domain/entities/document.py              ← Parts 1 & 5 (additive only — Gap 8)
    ChunkMetadata gains: section_title, heading_level, semantic_cluster, ocr_confidence, language

src/domain/repositories/document_intelligence_repository.py    ← Part 7 (new port)
    DocumentIntelligenceRepository ABC — save(document_id, summary), get_by_document(id)

src/infrastructure/database/postgres/
    document_intelligence_repository.py   Postgres adapter for the above
    migrations/versions/xxxx_add_document_intelligence.py   new table + new chunk columns

src/ui/pages/06_document_intelligence.py     ← Part 7
src/api/routes/documents.py                  ← add GET /documents/{id}/intelligence (additive route)

scripts/benchmark_chunking.py                ← Part 8 (reuses evaluation/offline/*)
data/eval_datasets/reports/                  ← benchmark output location

docs/HOW_IT_WORKS.md                         ← Part 9 update
```

---

## 6. Pipeline Integration — `IngestionPipeline.ingest()`

Current 8 steps stay in place; new steps are inserted between "Step 1: Load" and "Step 2: Enrich",
and "Step 3: Chunk" becomes a strategy call:

```
Step 1  loader.load(file_path)                         → RawDocument            (unchanged)
Step 1b ocr_detector.detect(raw_document, file_type)    → OCRDecision            (NEW)
Step 1c if required: ocr_provider.recognize(...)        → OCRResult, merged in   (NEW)
Step 1d layout_analyzer.analyze(raw_document)           → DocumentLayout         (NEW)
        → ParsedDocument(raw, ocr_metadata, layout)                              (NEW, unifies 1b-1d)
Step 2  enricher.enrich(parsed_document.raw.full_text, file_name)   (unchanged call, OCR-augmented text)
Step 3  chunking_strategy.chunk(document.id, parsed_document)        (NEW: ChunkingStrategy swap point —
                                                                       HybridChunkingPipeline in prod,
                                                                       ParentChildOnlyStrategy for A/B bench)
Step 4  embedding_strategy.retrieval_provider.embed_texts(...)       (same call shape, provider now
                                                                       comes from EmbeddingStrategy)
Steps 5-8   collection/index ensure, save, upsert, index             (unchanged)
        + intelligence_repo.save(document.id, parsed_document summary)   (NEW, after Step 8)
```

`OCRDetector` rules: extension-based skip for `.docx/.md/.html/.txt` (never scanned); for `.pdf`,
compare `raw_document.word_count / page_count` against a configurable threshold (low ratio ⇒
scanned ⇒ OCR); for image MIME types (`ImagePassthroughLoader` output — always empty text) ⇒
always OCR. This directly implements the Part 1 skip-vs-OCR examples.

`IngestionPipeline.__init__` gains new constructor params (`ocr_detector`, `ocr_provider`,
`layout_analyzer`, `intelligence_repo`); the existing `chunker: ParentChildChunker` param is
replaced by `chunking_strategy: ChunkingStrategy`, and `embedding_provider: EmbeddingProvider` by
`embedding_strategy: EmbeddingStrategy`. Both are call-site changes in
`api/dependencies.py._build_ingestion_pipeline()` only — `ingest()`'s try/except/status-tracking
control flow is not rewritten.

---

## 7. Build Order

```
Module 1  OCR Abstraction         ocr/{base,models,detector,tesseract_provider,registry}.py
Module 2  OCR Extras              paddle_provider.py (stub), unlimited_provider.py (stub) + doc write-up
Module 3  Layout Extraction       loaders/base.py additive fields → docling/unstructured population
                                   → layout/{base,models,docling_layout_analyzer,heuristic_layout_analyzer}.py
Module 4  ParsedDocument          parsing/{parsed_document,parsing_orchestrator}.py — wires 1-3 together
Module 5  Embedding Strategy      embedders/{bge_embedder,e5_embedder,registry,embedding_strategy}.py
Module 6  Hybrid Chunking         chunkers/{structure_chunker,semantic_chunker,chunk_validator,
                                   hybrid_chunking_pipeline,chunking_strategy}.py
Module 7  Chunk Metadata + DB     domain/entities/document.py additive fields, migration, chunk_repository update
Module 8  Pipeline Wiring         pipeline.py new steps, dependencies.py factories, ImagePassthroughLoader
Module 9  Intelligence Repo + API document_intelligence_repository.py (domain+infra), GET /documents/{id}/intelligence
Module 10 Streamlit UI            ui/pages/06_document_intelligence.py
Module 11 Benchmark               scripts/benchmark_chunking.py (reuses evaluation/offline/*)
Module 12 Docs                    HOW_IT_WORKS.md update, this doc's Module Log finalized
```

Each module: design note → code → unit tests (mocked deps, `tests/conftest.py` fixture style) →
integration test where real infra is touched (Postgres migration, Qdrant) → Module Log entry below,
exactly like Phase 3.

---

## 8. Verification Plan

- `pytest` full suite stays green after every module (coverage gate `--cov-fail-under=80` in
  `pyproject.toml` already enforces this).
- Module 3 verified by asserting `DoclingLoader`/`UnstructuredLoader`'s *existing* tests
  (`tests/unit/ingestion/`) pass unmodified — proves the additive-fields decision didn't break
  Phase 1–3.
- Module 6 verified against `tests/unit/ingestion/test_parent_child_chunker.py` staying green
  (proves `ParentChildChunker` itself wasn't touched) plus new tests for `HybridChunkingPipeline`.
- Module 11's benchmark script is the end-to-end proof this upgrade helps: run against
  `data/eval_datasets/`, compare `ParentChildOnlyStrategy` vs `HybridChunkingPipeline` on recall/
  precision/faithfulness/chunk count/indexing time — the Part 8 deliverable.
- UI (Module 10) manually checked in-browser (start Streamlit, upload a scanned PDF, confirm
  OCR/layout/chunk-hierarchy panels render) before calling Part 7 done.

---

## 9. Decisions (Resolved)

1. **Layout fidelity: extend loaders additively** (Gap 1) — real heading/list/table/footnote
   structure via new optional `RawDocument`/`TextBlock` fields, populated in `DoclingLoader`/
   `UnstructuredLoader` with additive lines only.
2. **OCR engines: Tesseract real now, PaddleOCR interface-only** (Gap 2) — matches this
   environment's confirmed CPU-only constraint.
3. **Baidu Unlimited OCR: design-only stub** (Gap 3) — real class against the interface,
   untested, documented from vendor sources since no credentials exist.
4. **This pass: design review only** — no implementation code ships with this document. Module 1
   starts in a follow-up turn.

Implementation proceeds module by module starting at Module 1, per the build order in §7.

---

## 10. Module Log

### Module 1 — OCR Abstraction (done)

**Files:** `src/ingestion/ocr/{__init__,models,base,detector,tesseract_provider,registry}.py`.
Tests: `tests/unit/ingestion/ocr/test_{detector,tesseract_provider,registry}.py` — 25 tests.

**What it is:** `OCRProvider` ABC (`recognize(file_path, language) -> OCRResult`); `OCRDetector.detect()`
implements the skip-vs-OCR rules from §6; `TesseractProvider` is the real, working default.

**Design choices worth flagging:**
- **`TesseractProvider` takes an injected `page_loader` + `image_to_data` callable**, not the
  `pytesseract`/`pdf2image` modules directly — same "dependency injection over construction"
  pattern `BGEReranker` uses for its `CrossEncoder`. `registry.py` is the only place that actually
  imports `pytesseract`/`pdf2image` (lazily, inside the factory functions), so unit tests never
  need the real Tesseract binary or poppler system packages installed.
- **`pytesseract` is not a direct pyproject dependency.** `unstructured[all-docs]` (already pinned)
  transitively installs `unstructured-pytesseract` — a maintained fork published under a different
  PyPI name. `pdf2image` was added explicitly since it has no naming collision. Confirmed via
  `uv lock` regenerating cleanly with zero conflicts once the redundant line was removed.
  **Correction (see the later "OCR_PROVIDER=tesseract had never actually run" follow-up entry in
  this log):** the claim that follows here originally — that this fork installs under the *same*
  `import pytesseract` module path — was wrong, and unit tests couldn't catch it because they mock
  `pytesseract`/`unstructured_pytesseract` out entirely by design. It installs as
  `unstructured_pytesseract` (a different top-level module name, API-compatible but a different
  import path), which meant `registry.py`'s real `import pytesseract` call was silently broken
  from Module 1 onward until fixed later.

**Example usage:**
```python
from src.config import get_settings
from src.ingestion.ocr.registry import get_ocr_provider
from src.ingestion.ocr.detector import OCRDetector

settings = get_settings()
detector = OCRDetector(min_words_per_page=settings.ocr_min_words_per_page)
decision = detector.detect(raw_document, file_type="pdf")
if decision.required:
    ocr_result = await get_ocr_provider(settings).recognize(file_path, language="en")
```

### Module 2 — OCR Extras (done)

**Files:** `src/ingestion/ocr/{paddle_provider,unlimited_provider}.py`, both registered in `registry.py`.

**What it is:** `PaddleOCRProvider` (real class, `paddleocr` imported lazily, unwired per Decision 2
— CPU-only environment) and `UnlimitedOCRProvider` (real Baidu General Text Recognition REST client
using `httpx`, per Decision 3 — untested, no credentials). Strengths/weaknesses/hardware/latency for
both are documented in each class's docstring rather than a separate doc file, so they stay next to
the code they describe.

### Module 3 — Layout Extraction (done)

**Files:**
- `src/ingestion/loaders/base.py`: additive `BoundingBox` dataclass + `TextBlock.{element_label,
  heading_level, is_footnote, bbox}` fields (all optional, defaulted).
- `src/ingestion/loaders/docling_loader.py`, `unstructured_loader.py`: populate the new fields.
- `src/ingestion/layout/{__init__,models,base,labeled_layout_analyzer,heuristic_layout_analyzer}.py`.

Tests: `tests/unit/ingestion/layout/test_{labeled_layout_analyzer,heuristic_layout_analyzer}.py`,
`tests/unit/ingestion/test_{docling_loader,unstructured_loader}.py` — 33 tests (the two loader test
files are net-new; neither loader had any test coverage before Phase 4A, so "existing tests stay
green" was verified by there being zero pre-existing tests to break, not by re-running old ones).

**Design choices worth flagging (found while building, not assumed upfront):**
- **`DoclingLoader` no longer merges text by page.** The original implementation joined every
  `doc.texts` item into one `TextBlock` per page, which is exactly what discarded Docling's own
  per-item `label`/`level`/`bbox` data. Rewritten to emit one `TextBlock` per Docling text item.
  This changes `RawDocument.full_text`'s exact whitespace (double-newline between every item now,
  not just between pages) — a deliberate, necessary trade-off for Gap 1, not a regression: no
  existing test asserted on `DoclingLoader`'s exact spacing (none existed), and `ParentChildChunker`
  tokenizes on the result regardless of paragraph spacing.
- **Renamed `docling_layout_analyzer.py` → `labeled_layout_analyzer.py`** from the design doc's
  original file tree. Once built, it became clear the analyzer only reads `TextBlock.element_label`
  — a label vocabulary both `DoclingLoader` and `UnstructuredLoader` populate — so naming it after
  one specific loader was misleading.
- **`RawDocument` did NOT gain `forms`/`outline` fields**, unlike the original design doc's file
  tree. Building `LabeledLayoutAnalyzer` made it clear that grouping raw labeled blocks into forms
  (adjacent `field_key`/`field_value` pairs) and a nested outline is analysis logic, not loader
  output — it belongs entirely in `DocumentLayout` (produced by the analyzer), not duplicated onto
  `RawDocument` too.
- **Unstructured's element categories are normalized to Docling's vocabulary** (`Title`→`title`,
  `ListItem`→`list_item`, etc., see `_LABEL_MAP` in `unstructured_loader.py`) so
  `LabeledLayoutAnalyzer` has one label vocabulary regardless of source loader. Unstructured has no
  distinct "footnote" category — a real, documented fidelity gap versus Docling.
- Confirmed against the actually-installed `docling-core` package (not assumed): `item.label` is a
  `DocItemLabel` enum (`.value` gives the lowercase string), heading level lives on
  `SectionHeaderItem.level`, and bbox coordinates are `prov.bbox.{l,t,r,b}`. Confirmed against
  `unstructured`: heading depth is `element.metadata.category_depth`.

### Module 4 — ParsedDocument + Parsing Orchestrator (done)

**Files:** `src/ingestion/parsing/{__init__,parsed_document,parsing_orchestrator}.py`.
Tests: `tests/unit/ingestion/parsing/test_parsing_orchestrator.py` — 5 tests.

**What it is:** `DocumentParsingService.process(raw_document, file_path, file_type, language) ->
ParsedDocument` runs `OCRDetector` → `OCRProvider` (only if required) → picks `LabeledLayoutAnalyzer`
if any text block carries a label, else `HeuristicLayoutAnalyzer` → wraps the result. OCR output
*replaces* (not appends to) the loader's `text_blocks`, since OCR only runs when the loader's own
extraction was already judged insufficient (Gap 7's `_merge_ocr_result`).

### Module 5 — Embedding Strategy (done)

**Files:** `src/ingestion/embedders/{bge_embedder,e5_embedder,registry,embedding_strategy}.py`.
`config.py`: `chunking_embedding_provider`, `bge_model_name`, `e5_model_name`.
Tests: `tests/unit/ingestion/test_{bge_embedder,e5_embedder,embedding_registry}.py` — 15 tests.

**What it is:** `BGEEmbeddingProvider`/`E5EmbeddingProvider` both satisfy the existing
`EmbeddingProvider` ABC unchanged (no interface change needed — it was already generic).
`get_embedding_provider(settings, role="chunking"|"retrieval")` mirrors `llm/registry.py`'s
role-based factory exactly. `E5EmbeddingProvider` applies the model's documented `"query: "`/
`"passage: "` prefix convention internally, since forgetting it silently degrades quality rather
than erroring — not something to leave to callers.

**Design choice worth flagging:** local `SentenceTransformer` models are cached at module level in
`registry.py` (`_bge_model`/`_e5_model` globals), since they're multi-hundred-MB downloads and the
registry function may be called more than once per process.

### Module 6 — Hybrid Chunking Pipeline (done)

**Files:**
- `src/ingestion/chunkers/{structure_chunker,semantic_chunker,chunk_validator,
  hybrid_chunking_pipeline,chunking_strategy}.py` (all new).
- `src/ingestion/chunkers/parent_child_chunker.py`: additive `count_tokens()` and `chunk_section()`
  methods — `chunk()` itself is completely unchanged.
- `src/domain/entities/document.py`: `ChunkMetadata` gains `section_title`, `heading_level`,
  `semantic_cluster`, `ocr_confidence`, `language` (all optional).

Tests: `tests/unit/ingestion/chunkers/test_{structure_chunker,semantic_chunker,chunk_validator,
hybrid_chunking_pipeline,chunking_strategy}.py` + 3 new tests appended to
`test_parent_child_chunker.py` for `chunk_section()` — 47 tests. Full suite after this module:
274/277 passing (3 pre-existing, unrelated Starlette-version failures in `test_main.py`).

**What it is:** `HybridChunkingPipeline.chunk(document_id, parsed_document)` runs
`StructureChunker.split()` (headings/paragraphs/tables/lists, Part 4 Step 1) →
`SemanticChunker.split()` (per-section sentence embeddings + adaptive-threshold boundary detection,
Step 2) → `ParentChildChunker.chunk_section()` per resulting segment (Step 3, token windowing +
parent/child hierarchy, **reused unchanged**) → `ChunkValidator.validate()` (Step 4, rejects
too-small/duplicate/whitespace/OCR-garbage/low-confidence chunks).

**Design choices worth flagging:**
- **Adaptive threshold, not fixed:** `SemanticChunker` computes
  `threshold = mean(similarities) - std_multiplier * stdev(similarities)` *per section*, so cut
  sensitivity adapts to how topically varied that specific section already is, rather than one
  absolute cosine-similarity number applied everywhere (Part 4's explicit requirement).
- **`ChunkValidator` returns rejection reasons, not just a pass/fail count** (`too_small`,
  `duplicate`, `whitespace_only`, `ocr_garbage`, `low_ocr_confidence`) — surfaced to the Document
  Intelligence UI (Part 7) and the benchmark script (Part 8) so "why was this chunk dropped" is
  answerable, not just "how many were."
- **`ParentChildOnlyStrategy`** (in `chunking_strategy.py`) is a thin adapter wrapping the
  pre-Phase-4A `ParentChildChunker.chunk()` call unchanged — the Part 8 A/B baseline. Both it and
  `HybridChunkingPipeline` satisfy the `ChunkingStrategy` protocol so `IngestionPipeline` never
  branches on which one it got (Gap 5).
- **Tables pass through both `StructureChunker` and `SemanticChunker` untouched** — splitting a
  table's markdown mid-row would corrupt it; they become one `DocumentChunk(chunk_type=TABLE)` each.

### Module 7 — Chunk Metadata Persistence (done)

**Files:** `src/infrastructure/database/postgres/models.py` (`DocumentChunkModel` +5 nullable
columns), `chunk_repository.py` (`save_batch`/`_to_entity` map the 5 new fields).
Tests: `tests/unit/infrastructure/test_chunk_repository.py` — 2 tests (first-ever test file for
this repository; none existed before Phase 4A).

**Discovery that changed the plan:** the design doc assumed an additive Alembic migration. Auditing
`src/api/main.py` and `migrations/` before writing one found this project has **zero** Alembic
migrations for *any* table across Phases 1-3 (`conversations`, `evaluation_runs`, etc. included) —
every table is bootstrapped via `Base.metadata.create_all()` at API startup
(`src/api/main.py:32`). `alembic.ini`/`env.py` are fully configured but unused in practice.
Introducing the project's first-ever migration for just these 5 nullable columns would be
inconsistent with how every other table in this codebase came to exist — Module 7 instead follows
the established `create_all()` pattern: update the SQLAlchemy model, nothing else. Flagged here
since it's a real deviation from §4 of this doc, not an oversight.

**Correction (found during end-to-end verification, see "Bug 1" under End-to-End Verification
below):** this reasoning missed that `create_all()` only creates *missing* tables — it never
alters one that already exists, which every real (non-brand-new) database has for
`document_chunks`. The project's actual first Alembic migration was written after all, specifically
to patch existing databases; `create_all()` remains correct for anyone starting from an empty
database.

### Module 8 — Pipeline Wiring (done)

**Files:**
- `src/ingestion/loaders/image_loader.py` (`ImagePassthroughLoader`, new).
- `src/ingestion/pipeline.py`: rewritten constructor (`chunker`→`chunking_strategy`,
  `embedding_provider`→`embedding_strategy`, +`parsing_service`, +optional
  `intelligence_recorder: IntelligenceRecorder | None`) and new Steps 1b-1d + Step 9 in `ingest()`.
  try/except/status-tracking control flow is unchanged.
- `src/config.py`: `semantic_chunk_std_multiplier`, `semantic_chunk_min_sentences`,
  `chunk_validator_min_chars`, `chunk_validator_min_ocr_confidence`; `allowed_file_types` gained
  image extensions.
- `src/api/dependencies.py`: `_build_ingestion_pipeline()` rewired to construct
  `EmbeddingStrategy`/`HybridChunkingPipeline`/`DocumentParsingService` and pass them in;
  `retrieval_provider` deliberately reuses the existing `_get_embedder()` singleton (not a fresh
  instance) so ingestion and query-time embedding never drift onto different models/instances.

Tests: `tests/unit/ingestion/test_image_loader.py` (2 tests) + `tests/unit/ingestion/
test_ingestion_pipeline.py` fixture rewired to the new constructor shape, +2 new tests for the
`intelligence_recorder` hook + no-recorder path; `tests/unit/api/test_dependencies.py` +2 tests
(`get_ingestion_pipeline` constructs / is a singleton, mocking `SentenceTransformer` the same way
the existing reranker test mocks `CrossEncoder`). Full suite: 282/285 passing (3 pre-existing).

**Design choice worth flagging:** `IntelligenceRecorder` is a local `Protocol` defined inside
`pipeline.py` rather than importing `DocumentIntelligenceRepository` (Module 9) — `IngestionPipeline`
has no hard dependency on the persistence-layer package that eventually satisfies it; anything with
a matching `async def save(...)` structurally qualifies.

### Module 9 — Document Intelligence Repository + API (done)

**Files:**
- `src/domain/value_objects/document_intelligence.py` (`DocumentIntelligenceSummary`,
  `LayoutSummary`, `SimilarityEdge`), `src/domain/repositories/document_intelligence_repository.py`
  (`DocumentIntelligenceRepository` ABC) — both domain-pure, no `ParsedDocument`/`DocumentChunk`
  (ingestion-layer types) referenced.
- `src/ingestion/parsing/intelligence_recorder.py` (`DocumentIntelligenceRecorder`) — the adapter
  that translates `ParsedDocument` + written chunks into a `DocumentIntelligenceSummary` and calls
  the domain repository; this is what actually satisfies `IngestionPipeline`'s `IntelligenceRecorder`
  Protocol.
- `src/infrastructure/database/postgres/{models.py +DocumentIntelligenceModel,
  document_intelligence_repository.py}` (Postgres adapter, upsert-on-conflict keyed by
  `document_id` so re-ingesting a document replaces its summary rather than duplicating it).
- `src/api/routes/documents.py`: `GET /documents/{id}/chunks` (new — no prior endpoint listed a
  document's chunks at all) and `GET /documents/{id}/intelligence`.
- `src/api/dependencies.py`: `get_intelligence_repository()` singleton, wired into
  `_build_ingestion_pipeline()`.

Tests: `tests/unit/ingestion/parsing/test_intelligence_recorder.py` (6 tests) + `tests/unit/
infrastructure/test_document_intelligence_repository.py` (3 tests) — 9 tests. No route-handler test
was added (`get_document`/`list_documents`/etc. have never had direct FastAPI `TestClient` tests
in this codebase either — route wiring isn't unit-tested here, only the repositories/services
behind it).

**Design choices worth flagging:**
- **The "semantic similarity graph" is cosine similarity between consecutive *already-embedded*
  chunks** (by `position`), computed once at persistence time from the real retrieval embeddings
  Step 4 already produced — not a re-embedding pass, and not a trace of the sentence-level
  boundaries `SemanticChunker` used internally (which are discarded once segments are finalized).
  This was a deliberate scope decision: wiring sentence-level trace data through
  `ParentChildChunker.chunk_section()` and back out would have meant threading a new return value
  through every layer of Module 6 for a visualization feature: a documented scope cut, same spirit
  as Phase 3's exact-hash-only dedup.
- **`DocumentIntelligenceRepository` never imports `ParsedDocument`/`DocumentChunk`.** Keeping the
  ingestion-layer→domain-layer translation in `DocumentIntelligenceRecorder` (ingestion layer)
  rather than the repository ABC itself (domain layer) preserves the dependency direction every
  other repository in this codebase already follows.

### Module 10 — Streamlit Document Intelligence Page (done)

**Files:** `src/ui/pages/06_document_intelligence.py`.

**What it is:** Document picker (indexed documents only) → 5 tabs: OCR (engine/confidence/timing or
a "skipped" message), Layout (table/figure/list/form/footnote counts + recursive outline render),
Chunk Hierarchy (parent chunks as expanders, children nested inside, tables/standalone separately),
Embeddings (chunking-role vs retrieval-role model names), Semantic Graph (Plotly line chart of the
`semantic_graph` edges from Module 9). No unit tests — consistent with this codebase's existing
convention of not unit-testing Streamlit pages (`pyproject.toml`'s coverage config already omits
`src/ui/*`, and no other page under `src/ui/pages/` has a test file).

**Scope note:** "page preview" (Part 7's literal wording) is not a rendered image thumbnail — this
project has no page-image capture/storage pipeline for any document, Phase 4A included. The Layout
tab's outline (with page numbers per heading) is the closest honest equivalent shipped here, rather
than fabricating an image-preview feature that doesn't have real data behind it.

### Module 11 — Benchmark Script (done)

**Files:** `scripts/benchmark_chunking.py`.

**What it is:** Ingests a small built-in 3-document corpus (covering `golden_set_v1`'s existing
topics: ML production failures, RAG evaluation, hybrid retrieval/chunking) through two fully
isolated pipelines — `ParentChildOnlyStrategy` ("current") vs `HybridChunkingPipeline` ("hybrid") —
each writing to its own suffixed Qdrant collections and Elasticsearch index so the two variants
never share state, while reusing the *same* embedding/LLM/reranker configuration for both (only
chunking differs — an apples-to-apples comparison). Runs every `golden_set_v1` question through
each variant's `QueryPipeline.inspect()`/`.answer()`, scores with the existing
`evaluation/offline/metrics.py` functions (`score_faithfulness`, `score_answer_relevancy`,
`score_context_relevancy`, `score_answer_correctness` — no new scoring logic invented), and reports
chunk count, indexing time, an embedding-cost estimate, citation count, and average query latency
side by side. Cleans up its own documents/collections/indices afterward unless `--keep` is passed.

**Design choices worth flagging:**
- **Not run automatically as part of this build.** It makes real LLM API calls for every question
  against both variants (enrichment, query intelligence, reranking, compression, answer generation,
  4 scoring calls per question) — a real cost against whichever provider `src/config.py` has
  configured. Verified only via import smoke-test (`python scripts/benchmark_chunking.py --help`
  resolves every import with no errors); left for the user to run deliberately.
- **No hand-labeled relevant-chunk annotations exist for the built-in corpus**, so "retrieval
  recall/precision" is reported via `context_relevancy` (an LLM-judge proxy already used elsewhere
  in this codebase's evaluation framework), not a true labeled-set recall/precision number — printed
  as an explicit caveat in the report output, not silently substituted.

### End-to-End Verification (done)

Full unit suite: **290/293 passing** throughout (3 pre-existing, unrelated Starlette-version
failures in `tests/unit/api/test_main.py` — present before Phase 4A started, not touched by it).

Real Docker rebuild (`docker compose build api`), real document upload through
`POST /api/v1/documents` against the actual running Postgres/Qdrant/Elasticsearch/Redis stack, and
real reads of `GET /documents/{id}/intelligence` + `GET /documents/{id}/chunks` — two real
production bugs were found and fixed this way, neither of which any mocked unit test could have
caught:

**Bug 1 — `document_chunks` never actually gained the 5 new columns in this environment.**
Module 7's log entry rationalized skipping Alembic because every Phase 1-3 table was bootstrapped
via `Base.metadata.create_all()`, which is true -- but `create_all()` only creates *missing*
tables, it never alters an existing one. This dev database already had a `document_chunks` table
(from a document ingested before Phase 4A), so `create_all()` silently skipped it entirely on
every subsequent startup, and the very first real ingestion attempt failed with
`asyncpg.exceptions.UndefinedColumnError: column "section_title" of relation "document_chunks"
does not exist`. **Fix:** wrote the project's actual first Alembic migration
(`migrations/versions/0001_phase4a_document_intelligence.py`), using `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` throughout so it's safe to run against either a
fresh database (where `create_all()` already added everything) or an existing pre-Phase-4A one
(where the migration is the only thing that actually adds them). Ran `alembic upgrade head`
against the live dev database to confirm the fix; both documents that failed under the bug were
correctly marked `status="failed"` (not stuck), confirming `IngestionPipeline`'s existing
try/except error handling worked correctly even under this failure.

**Bug 2 — first upload after any container start blocked the entire API, not just that request.**
`get_ingestion_pipeline()` constructs `EmbeddingStrategy`'s chunking-role provider synchronously
(`SentenceTransformer("BAAI/bge-m3")` -- a ~2GB cold download+load on first use), and it was being
evaluated as an eager argument to `background_tasks.add_task(_run_ingestion,
get_ingestion_pipeline(), ...)` inside the `POST /documents` route handler. Since Python evaluates
call arguments before the call, this ran the multi-minute blocking download **synchronously inside
the request handler, on the single-threaded event loop** -- freezing every other route (including
unrelated `GET /health` calls on the same worker) for as long as the download took. Observed
directly: `curl` uploads timed out client-side, `unstructured_load_start` fired but nothing else
did, and even simple `GET /api/v1/documents` calls hung with zero bytes received. **Fix:** added an
explicit warm-up step to `main.py`'s `lifespan()` startup (`await
asyncio.to_thread(get_ingestion_pipeline)`), run once at container start, after
`ensure_cache_collection()` -- the same precedent that function already established for "things
that must exist before the first request, not lazily on it." Verified by restarting the container
and confirming `ingestion_pipeline_warmed` logs before `Application startup complete`, and that a
subsequent upload returned `202` immediately with no stall.

**Bug 3 — a real user-uploaded, image-heavy PDF reproducibly OOM-killed a uvicorn worker (found
after initial sign-off, via a real user upload).** Docling runs its own internal OCR engine
(RapidOCR, 3 ONNX models) plus a ~770-weight layout/table-structure model the first time it
encounters image content in a PDF -- a real, one-time memory spike. Confirmed via a standalone,
single-process run of `DoclingLoader.load()` against the exact same file (succeeded cleanly,
~78s, 2 pages/1680 blocks/2 images) that the loader itself is not at fault. Inside the full app,
with `--workers 2` each independently holding a resident BGE-M3 (Module 5's chunking-role default)
alongside Postgres/Elasticsearch/Qdrant/Redis/Streamlit sharing one Docker Desktop memory budget,
that spike had no headroom and the OS silently killed the worker mid-task -- no Python exception,
no traceback, the document permanently orphaned in `status="processing"`. Reproduced twice
(confirmed not a fluke) before applying the fix. **Fix:** `docker/api.Dockerfile` CMD dropped from
`--workers 2` to `--workers 1` (documented inline in the Dockerfile). Re-verified against the same
file after rebuilding: indexed successfully, zero worker deaths, `figures_count: 2` correctly
detected, non-empty `semantic_graph` this time (this document had enough child chunks with
embeddings, unlike the earlier tiny test doc). This is an environment/resource-sizing issue, not a
logic bug in any Phase 4A module -- flagged here because `--workers 2` was this project's existing
default (Phase 1-3), not something Phase 4A set, but Phase 4A's added resident models (BGE-M3,
Docling's own OCR/layout models) are what pushed it over the edge.

**What was actually confirmed working, end to end, against real infra (not mocks):**
- OCR correctly skipped for a `.md` upload (`ocr_ran: false`, reason `"'md' is always text-native"`).
- A real scanned/image-containing tax-form PDF: `figures_count: 2` correctly detected, 4 headings +
  30 lists extracted, OCR correctly judged unnecessary (Docling's own extraction already yielded
  sufficient real text), and a populated `semantic_graph` with real cosine-similarity edges.
- `LabeledLayoutAnalyzer` correctly extracted 5 headings with correct nesting (one H0 title with 4
  H1 children), 1 table, 2 distinct lists (bullet list + numbered list, correctly *not* merged
  since a table row separated them structurally).
- `HybridChunkingPipeline` produced one parent chunk per section, each carrying the correct
  `section_title`/`heading_level`; the table became its own `TABLE` chunk.
- `ChunkValidator`'s duplicate rejection fired exactly as designed: this test document's sections
  were all shorter than `child_chunk_size` (256 tokens), so each section's "child" split produced
  content identical to its parent -- correctly rejected as `duplicate` rather than double-indexed
  (visible as 4 rejected chunks per document in `hybrid_chunking_complete` logs).
- `EmbeddingStrategy`'s dual roles both appeared correctly in the persisted summary:
  `embedding_model_chunking: "BAAI/bge-m3"`, `embedding_model_retrieval: "text-embedding-3-large"`.
- `GET /documents/{id}/intelligence` returns `404` for a document indexed before Phase 4A existed
  (confirmed against the pre-existing `ml_at_prod.pdf` document already in this dev database),
  and full real data for one indexed after the fixes above.
- Streamlit `streamlit` image rebuilt and container confirmed reachable (`GET /` and the new
  `Document_Intelligence` page route both return `200`); full in-browser interaction wasn't
  verified in this text-only environment -- confirmed only that the page loads without a
  server-side exception at the HTTP level, not that every tab renders correctly with real data.
- Test documents created during this verification pass were deleted afterward; the dev database
  was left exactly as found otherwise (one pre-existing indexed document, untouched).

### Follow-up — OCRDetector made per-page, not document-wide average (done)

**Trigger:** the tax-form PDF above (Bug 3) happened to work out because Docling's own internal
OCR covered the sparse page well enough that the document-wide average never even needed to catch
it. That's luck, not a guarantee -- `OCRDetector.detect()` computed one `words_per_page` average
across the whole document, so a document with several text-rich pages and one genuinely-blank
scanned page would average out fine and skip OCR for the one page that actually needed it. Caught
by inspecting this PDF's actual per-page word counts directly (`page 2: 1290 blocks / 2268 words`)
after being asked to double-check "skip" was the right call, not by any automated check.

**Files:** `src/ingestion/ocr/detector.py` (`OCRDecision` gains `pages_required: list[int]`;
`_find_sparse_pages()` replaces the single-average calculation), `src/ingestion/ocr/base.py` +
`tesseract_provider.py` + `paddle_provider.py` + `unlimited_provider.py` (`recognize()` gains an
optional `pages: list[int] | None` filter), `src/ingestion/parsing/parsing_orchestrator.py`
(`_merge_ocr_result()` now merges per-page -- keeps a non-OCR'd page's original, labeled blocks
untouched, only replacing the specific pages OCR actually covered).
Tests: `test_detector.py` gained a `test_mixed_document_only_flags_the_sparse_page` case (and its
`_raw_doc` fixture was rewritten to distribute words per-page realistically, since the old
single-mega-block fixture couldn't exercise per-page logic at all); `test_tesseract_provider.py`
gained a `pages` filter test; `test_parsing_orchestrator.py` gained
`test_mixed_document_ocrs_only_the_sparse_page_and_keeps_the_rest`, asserting the sparse page gets
OCR'd while the rich page's structural labels (and therefore `LabeledLayoutAnalyzer` selection)
survive. 4 new tests; full suite: 294/297 (same 3 pre-existing, unrelated failures).

**Design choice worth flagging:** `TesseractProvider` still renders every PDF page to an image via
`pdf2image` before filtering to the requested `pages` -- the filter happens *after* rendering, only
skipping the actual Tesseract OCR pass (the expensive part) on pages that don't need it, not the
page-rendering step. A full fix would need `pdf2image`'s `first_page`/`last_page` range params,
which don't support an arbitrary discontiguous page list cleanly; deferred as a documented,
minor inefficiency rather than a correctness issue.

Re-verified against the same PDF after rebuilding: per-page density on both pages is actually well
above threshold (page 2's 2268 words alone clears it), so this specific document's OCR-skip
decision is unchanged -- the fix's effect is only visible on genuinely mixed documents, covered by
the new unit test above.

### Follow-up — DoclingLoader/UnstructuredLoader were blocking the entire event loop (done)

**Trigger:** a real user re-upload of the same tax-form PDF appeared to "disappear" while
`status="processing"` -- the API stopped responding to *any* request (including plain `GET
/api/v1/documents` and `GET /api/v1/health`) for the ~60-90 seconds Docling took to process the
file. Root cause: `DoclingLoader.load()` and `UnstructuredLoader.load()` are declared `async def`,
but their actual work (`DocumentConverter.convert()`, `unstructured.partition()`) is synchronous,
CPU-bound code called directly -- never `await`-ed, never offloaded. `async def` alone does not
make blocking code non-blocking; it just means the function *can* contain `await` points, and
neither of these did. This bug predates Phase 4A (Phase 1/2 code, untouched until now), but was
masked by `--workers 2` (Bug 3's fix): a second worker could still answer requests while the first
was wedged. Dropping to `--workers 1` (to fix the OOM crash) removed that mask and exposed the
freeze directly -- one fix surfaced a second, independent bug. Confirmed via a watched, controlled
re-upload (polling Postgres directly every 3s through the full ~90s ingestion) that the document
row itself was never actually lost or duplicated; "gone" was the whole API being unreachable, not
data loss.

**Files:** `src/ingestion/loaders/docling_loader.py`, `unstructured_loader.py` -- both `load()`
methods now do only `return await asyncio.to_thread(self._parse, file_path)`; all the actual
parsing logic moved unchanged into a new private `_parse()` method on each class. No behavior
change to the parsing logic itself, confirmed by all 7 existing loader tests passing unmodified
(mocks patch the same module-level targets; `unittest.mock.patch` patches the attribute globally,
not thread-locally, so this works transparently across the `to_thread` boundary).

**Verification:** rebuilt, redeployed, re-uploaded the same PDF, and fired 6 concurrent `GET
/health` requests every 2 seconds *while* ingestion was running in the background -- all returned
`200` in ~100ms each, versus being completely unreachable for the ingestion's full duration before
the fix. Ingestion itself still completed correctly (`status="indexed"`, same
`figures_count`/`headings`/`semantic_graph` results as the earlier verification). Full suite:
294/297 (same 3 pre-existing, unrelated failures) -- no regression from the refactor.

**Design note:** `TesseractProvider.recognize()` already used `asyncio.to_thread` correctly from
Module 1 (matching `BGEEmbeddingProvider`'s pattern) -- this bug was specific to the two loaders,
which predate the "wrap CPU-bound work in `to_thread`" convention Phase 4A's own new code
consistently followed. Worth a project-wide audit later: any other pre-Phase-4A `async def` that
calls synchronous, CPU-bound code directly (LLM provider SDKs are genuinely async via httpx, so
likely not an issue there, but not re-verified here) would have the same latent bug, just not yet
observed under `--workers 1`.

### Follow-up — OCR_PROVIDER=tesseract (the default) had never actually run, ever (done)

**Trigger:** asked directly "how does OUR OCR help, given Docling has its own internal OCR" --
every real document tested through Phase 4A so far (both PDFs) had Docling's own internal OCR
already produce enough text, so the *supplemental* `OCRDetector`/`TesseractProvider` layer this
project actually built had never once been exercised end-to-end. Set out to prove it works by
uploading a real image with real rendered text (`.png`, generated via PIL, three sentences of
policy text) -- the one case guaranteed to route 100% through `ImagePassthroughLoader` +
`TesseractProvider` with zero Docling/Unstructured involvement. Found **three** independent, real
bugs stacked on top of each other, each hiding the next:

**Bug 1 -- wrong import name.** `src/ingestion/ocr/registry.py`'s `_image_to_data()` did
`import pytesseract`. Confirmed live: `pytesseract` is not importable in this environment at all
(`ModuleNotFoundError`) -- Module 1's original design-review claim that
`unstructured[all-docs]` transitively provides an `import pytesseract`-compatible module was
wrong on the module name specifically. What's actually installed is `unstructured_pytesseract`
(underscore, different top-level module), confirmed API-compatible
(`image_to_data`/`Output`/`get_tesseract_version` all present) but under a different import path.
**Fix:** `import unstructured_pytesseract as pytesseract`. Both the Module 1 pyproject.toml
comment and this file's own earlier "Module 1" log entry repeated the same wrong claim -- corrected
in place rather than left standing, since a wrong comment that sounds confident is worse than no
comment.

**Bug 2 -- the `tesseract` binary and `poppler-utils` were never installed in the runtime image at
all.** `pytesseract`/`unstructured_pytesseract` is a thin wrapper that shells out to a real
`tesseract` CLI binary; `pdf2image` (used to render PDF pages to images before OCR) shells out to
poppler's `pdftoppm`. Neither `tesseract-ocr` nor `poppler-utils` were ever added to
`docker/api.Dockerfile`'s apt-get install list -- confirmed live (`which tesseract` → not found).
Even with Bug 1 fixed, every real OCR call would have raised immediately on a missing binary.
**Fix:** added both packages to the Dockerfile's runtime-stage apt-get install list, alongside the
existing opencv/curl dependencies.

**Bug 3 -- image uploads were rejected by the API before ever reaching the ingestion pipeline.**
`ALLOWED_FILE_TYPES` in the actual `.env` (and `.env.example`) still read
`pdf,docx,txt,md,html` -- Module 8 updated `Settings.allowed_file_types`'s *Python default* to
include image extensions, but never updated the `.env`/`.env.example` files, and pydantic-settings
loads `.env` values over the Python default when present. Every image upload was returning
`422 File type 'png' is not supported` at the route layer, before `ImagePassthroughLoader` or
`OCRDetector` ever ran. **Fix:** updated `ALLOWED_FILE_TYPES` in both `.env` and `.env.example` to
match the intended default.

**Why none of this surfaced during the original Module 1/8 work or the earlier end-to-end
verification pass:** every unit test for `TesseractProvider`/`registry.py` mocks
`pytesseract`/`unstructured_pytesseract` out entirely (by design, for testability -- see Module 1's
DI-over-construction rationale), so a wrong import name inside a function that's never actually
called in tests is invisible to the suite. And every real PDF uploaded during the earlier live
verification pass happened to have enough text after Docling's own extraction that `OCRDetector`
never actually reached the point of calling `OCRProvider.recognize()` at all -- Bugs 1 and 2 were
silently latent the whole time. Bug 3 additionally meant the one input type guaranteed to trigger
it (a standalone image) couldn't even be uploaded to begin with. Three independent gaps, each
individually silent, that only a deliberate "make the supplemental path actually fire" test
uncovered.

**Verification (after all three fixes, one real end-to-end run):**
```
upload vacation_policy.png (a PIL-generated image, 3 sentences of real rendered text)
  → image_passthrough_load          (ImagePassthroughLoader, zero native text, as designed)
  → ocr_required   reason="image file has no extractable text"  pages=[1]
  → tesseract_ocr_done   pages=1   elapsed_ms=1592.5
  → GET .../intelligence:  ocr_engine="tesseract"  ocr_ran=true
                            ocr_confidence_avg=0.9577  ocr_language="en"
  → GET .../chunks:  content exactly matches all 3 sentences rendered into the image,
                      word-for-word, 0 transcription errors (30/30 words correct)
```
Full suite after all three fixes: 294/297 (same 3 pre-existing, unrelated failures) -- confirmed
the `unstructured_pytesseract` import alias doesn't break anything mocked in tests.

**Side-finding, not a bug:** this test document ended up with exactly one `parent` chunk (its
whole content fit under `child_chunk_size`, so Step 3's would-be child was deduped away, same
shape as the earlier refund-policy example) and **zero embedded chunks**
(`"embeddings": 0` in the ingestion-complete log) -- since only `child`/`table` chunks get
embedded, a document this short is indexed and keyword-searchable (Elasticsearch) but not
vector-searchable (Qdrant) at all. Correct behavior given the existing design, just worth knowing:
very short documents are BM25-only.
