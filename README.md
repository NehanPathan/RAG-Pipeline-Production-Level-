# RAG, twice

Two production-grade retrieval-augmented generation systems in one repository.
They are not alternatives — they are the same ideas built twice, deliberately, and
the difference between them is the point.

| | [`rag-core/`](rag-core/) | [`steel-doc-intelligence/`](steel-doc-intelligence/) |
|---|---|---|
| **What it is** | a general RAG platform, built from scratch | a steel & CAD document intelligence platform |
| **Frameworks** | **none** — no LangChain, no LangGraph | LangChain + LangGraph |
| **Orchestration** | a hand-written workflow engine | LangGraph `StateGraph` |
| **LLM access** | provider clients written directly | LangChain chat models |
| **Interface** | Streamlit | React 19 + Vite + TypeScript |
| **Domain** | any documents | steel drawings, schedules, DXF/DWG |
| **Size** | 20k lines, 81 test files | 34k lines Python + 21k TypeScript, 118 test files |

**Read `rag-core/` to understand how RAG works. Read `steel-doc-intelligence/` to see
what it takes to make it real for one industry.**

---

## Why two

Frameworks are wonderful right up to the moment something breaks inside one. So the
first system was built without any: every embedding call, every retry, every step of
the workflow engine is code you can open, read and step through. Nothing is hidden
behind a decorator. When retrieval returns the wrong chunk, you can find out why by
reading, because there is no layer you did not write.

That version is `rag-core/`. It is not a tutorial and not a toy — it has RBAC, four
level data clearance, an append-only audit log, PII redaction, a risk register that
CI validates against the code, and an evaluation gate. It is a system you could run.

`steel-doc-intelligence/` began as a copy of it and then diverged, replacing the
hand-written layers with LangChain and LangGraph, adding a React front end, and
teaching it a domain: reading CAD files as engineering documents rather than as
piles of lines and text.

**What that swap actually bought, and cost, is visible here in a way it usually is
not** — because both versions are complete, tested, and sitting side by side. The
files that exist only in `rag-core/` are exactly the ones frameworks replaced:

```
rag-core/src/workflow/{engine,node,context}.py      → LangGraph StateGraph
rag-core/src/llm/providers/{openai,anthropic}_*.py  → LangChain chat models
rag-core/src/ingestion/embedders/{bge,e5,openai}_*  → LangChain embeddings
```

Everything else — governance, retrieval, chunking, evaluation — was written once and
carried across, which is why roughly 70% of the two codebases is still shared.

---

## What each one does

Both share a core: documents in, chunked, embedded, stored three ways; questions
out, routed, retrieved, reranked, answered with citations that are validated
against what was actually retrieved.

### `rag-core/` — the shared foundation

* **Ingestion** — PDF, DOCX and images. OCR only on pages that need it, layout
  analysis for headings, tables and figures, then four-pass chunking (structure →
  semantic → parent/child → validate).
* **Retrieval** — vector search and BM25 run in parallel and merge by Reciprocal
  Rank Fusion, then a reranker reads query and candidate together and reorders.
  A semantic cache short-circuits repeat questions.
* **Answering** — streamed over SSE with `[n]` citations checked against the
  retrieved chunks. No supporting passage means a refusal, not a guess.
* **Routing and tools** — deterministic rules decide before any model call
  whether a question needs the corpus at all; calculator, datetime, translation,
  web search and a guarded SQL tool handle the ones that do not.
* **Governance** — Firebase auth, RBAC, four-level clearance enforced as a
  pre-filter *and* re-checked after retrieval, append-only audit, PII redaction,
  retention, rate limiting, and a risk register CI validates against the code.
* **Evaluation** — an LLM-judge suite with policy floors and a gate that fails
  the build when quality drops.
* **Interface** — Streamlit, including a retrieval inspector that shows what each
  stage actually returned.

### `steel-doc-intelligence/` — everything above, plus a domain

* **Reads CAD as documents.** A DXF is geometry, not prose. It assembles leader
  chains to work out which label belongs to which piece of steel, groups repeated
  blocks into symbols, and finds drawn schedules by structure and content
  together — declining a table it cannot read rather than guessing its rows.
* **Steel entities** — section designations, grades, bolt and weld specs, piece
  marks, drawing numbers, extracted by pattern and gazetteer so `ISMB300`,
  `ISMB 300` and `I.S.M.B.-300` all retrieve the same chunk.
* **Vision as a last resort.** When the answer is on the sheet but not in its
  text — an unlabelled symbol, a hatch — it renders *only that region* and asks a
  vision model. A low-confidence reading is discarded, and a vision reading can
  never override an exact value read from the file.
* **Projects and revisions.** Drawings supersede each other; search defaults to
  the latest revision, because answering from a superseded sheet is worse than
  not answering.
* **Content screening and quarantine.** An upload that is not an engineering
  document is held, not deleted, with the reason recorded so a steward can
  disagree and release it — after which it is screened again.
* **Background ingestion** on an arq worker with retries, so a restart mid-parse
  does not strand a document in `processing` forever.
* **Interface** — React 19 with a sheet viewer and highlight overlay, a faceted
  search sidebar, an ops section for quality, access and governance, and voice
  input for dictating a question.

---

## Where to start

**New to RAG?** Start with [`rag-core/docs/HOW_IT_WORKS.md`](rag-core/docs/HOW_IT_WORKS.md).
It follows one real document through every station of the pipeline with the actual
output at each step, then does the same for a question. No prior knowledge assumed.

**Want the domain system?** Start with
[`steel-doc-intelligence/docs/WALKTHROUGH.md`](steel-doc-intelligence/docs/WALKTHROUGH.md).
It walks a drawing in and a question out, naming the file responsible at each stop
and giving you a command to check it yourself.

**Comparing the two?** Read `rag-core/src/workflow/engine.py` and then
`steel-doc-intelligence/src/retrieval/graph/builder.py`. Same job: a hundred and seventy
lines of explicit machinery against a declarative graph. Neither is obviously better and the
tradeoff is the interesting part.

---

## Running either one

Each project is self-contained: its own compose file, its own `Makefile`, its own
dependencies. Nothing at the repository root needs to run first.

```bash
cd rag-core                 # or: cd steel-doc-intelligence
make dev                    # brings up the whole stack
make ci                     # lint, types, tests, governance checks
```

Both need a `.env` — copy the project's `.env.example` and fill it in. **Real
credentials never belong in this repository**; `config/` and every `.env` are
git-ignored, and `scripts/set_secret.py` exists so a key never has to pass through
your shell history.

---

## Documentation

Each project keeps its own docs, because each is a working system rather than a
chapter of one.

**`rag-core/docs/`**

* `HOW_IT_WORKS.md` — the full pipeline in plain English, with worked examples and
  real per-request costs
* `architecture/` — thirteen reference documents: schema, API design, data flow,
  risk, cost, roadmap
* `governance/` — risk register, data card, model card, authentication

**`steel-doc-intelligence/docs/`**

* `WALKTHROUGH.md` — the guided tour of the whole project, front end included
* `HOW_IT_WORKS.md` — inherited from `rag-core`, still accurate for the shared core
* `architecture/` — the above plus the steel-specific design: CAD understanding,
  the domain model, what a drawing contains that we could not previously read
* `governance/` — the same framework, extended for engineering drawings and
  commercially sensitive client names

The governance documents are machine-checked. `scripts/check_governance.py` fails
the build when the risk register names a metric that no longer exists or claims a
control that was deleted, so those documents cannot quietly go stale.

---

## What both systems agree on

Whatever the implementation, both were built on the same few convictions.

**"I don't know" beats a confident wrong answer.** Both refuse rather than
speculate: no retrieved context means no answer, and an answer citing none of its
sources is withheld. In the steel system this extends to the drawings themselves —
an unreadable table is a missing table, never a guessed one.

**Deterministic first, model last.** Regex, geometry and lookup tables come before
any model call. Models are used where they are genuinely better and nowhere else,
because a pattern that either matches or does not is cheaper, faster, reproducible
and auditable.

**Access control belongs in the query, not after it.** Clearance and tenancy are
pushed into the vector and keyword searches as filters. Filtering results afterwards
means the database briefly handed you rows you were not allowed to see.

**Roles come from our database, never from the token.** Authentication proves who
you are; what you may do is read server-side on every request.

**Nothing is deleted, things are marked.** Quarantine, supersede, soft-delete — a
reviewer can release a held file in seconds and cannot recover a refused upload.

---

## Licence

MIT, in both projects. See `rag-core/LICENSE` and `steel-doc-intelligence/LICENSE`.
