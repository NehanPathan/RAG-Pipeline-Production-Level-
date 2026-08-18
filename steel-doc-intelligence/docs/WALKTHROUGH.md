# A walk through the whole project

This is the "sit next to me and I'll show you" document. No prior knowledge assumed.
We follow two journeys end to end — **a drawing going in**, and **a question coming
out** — and at every stop you get three things:

* **what happens** in plain words,
* **which file** does it, and
* **how to check it yourself**, with a command you can actually run.

Read it top to bottom the first time. After that it works as an index.

> If you want the reference material instead of the tour, the `docs/architecture/`
> folder has fifteen focused documents (schema, API design, cost, risk). This one
> is the map that tells you which of those you need.

---

## Contents

1. [What this system is](#1-what-this-system-is)
2. [The map of the repo](#2-the-map-of-the-repo)
3. [Get it running so you can follow along](#3-get-it-running-so-you-can-follow-along)
4. [Journey one — a drawing goes in](#4-journey-one--a-drawing-goes-in)
5. [Journey two — a question comes out](#5-journey-two--a-question-comes-out)
6. [The things that say no](#6-the-things-that-say-no)
7. [The front end](#7-the-front-end)
8. [Five rules that explain most decisions](#8-five-rules-that-explain-most-decisions)
9. [Why each tool, and what we said no to](#9-why-each-tool-and-what-we-said-no-to)
10. [How to check anything](#10-how-to-check-anything)
11. [When something breaks, look here](#11-when-something-breaks-look-here)

---

## 1. What this system is

You give it engineering documents — steel drawings, schedules, specifications,
transmittals, PDFs, scans, native CAD files. It reads them, understands enough to
find things again, and answers questions about them **quoting the sheet the answer
came from**.

The part that matters most: when it does not know, it says so. It would rather
answer nothing than answer confidently and wrongly about somebody's structural
drawing. Almost every design decision in here falls out of that one preference.

Three numbers to set expectations: about **34,000 lines** of Python across 276
files, **21,000 lines** of TypeScript across 76, and **1,490 backend tests** plus
**101 frontend ones**.

---

## 2. The map of the repo

```
steel-doc-intelligence/
├── src/                     the backend
│   ├── api/                 HTTP surface — FastAPI routes
│   ├── domain/              the nouns: Document, Chunk, Region, Sensitivity
│   ├── ingestion/           everything that happens to a file on the way in
│   ├── retrieval/           everything that happens to a question on the way out
│   ├── routing/             deciding how a question should be answered at all
│   ├── governance/          the rules: who may read what, and when we refuse
│   ├── infrastructure/      the adapters: Postgres, Qdrant, Elasticsearch, S3
│   ├── jobs/                background work (the arq worker)
│   ├── evaluation/          measuring whether answers are any good
│   └── monitoring/          logs, metrics, traces
├── frontend/                the React app
├── tests/unit/              1,490 tests, mirroring src/
├── docs/                    this file, plus architecture and governance
├── scripts/                 one-off and operational tools
└── docker-compose.yml       the whole stack
```

**The shape to hold in your head.** `domain/` knows nothing about databases.
`infrastructure/` knows about databases but nothing about business rules. Everything
in between talks to `domain/` through interfaces. That is why you can read
`src/domain/entities/document.py` and understand what a document *is* without
knowing a single thing about Postgres.

---

## 3. Get it running so you can follow along

```bash
make dev            # brings up every service and rebuilds
```

Once it settles you have:

| What | Where | What it is for |
|---|---|---|
| Frontend | http://localhost:15173 | the app |
| API + docs | http://localhost:18000/docs | every endpoint, interactive |
| Postgres | localhost:15432 | documents, chunks, jobs, audit |
| Qdrant | http://localhost:16333/dashboard | the vectors |
| Elasticsearch | http://localhost:19200 | the keyword index |
| Grafana | http://localhost:13000 | dashboards |

**Check it is alive:**

```bash
curl http://localhost:18000/api/v1/health
```

The `/docs` page is worth ten minutes on its own — it is generated from the code, so
it is never out of date.

---

## 4. Journey one — a drawing goes in

One file, from upload to searchable. The whole sequence lives in
**`src/ingestion/pipeline.py`** — open it beside this section and the two will line
up step for step.

### Stop 1 — the door

`POST /api/v1/documents` in **`src/api/routes/documents.py`**.

The file is streamed to storage, a `Document` row is created with status `pending`,
and a job is queued. The upload returns immediately — it does not wait for
processing, because processing a large drawing takes minutes.

*Check it:* upload something through the UI, then `GET /api/v1/documents` and watch
`status` move from `pending`.

### Stop 2 — the worker picks it up

**`src/jobs/worker.py`**, running in its own container.

Work is queued in Redis using **arq**. The reason there is a queue at all: an earlier
version did this work inside the web request, and if the process restarted mid-way a
document sat in `processing` forever with nobody to finish it. A queue survives
restarts and retries.

*Check it:* `docker compose logs -f worker`

### Stop 3 — read the file

**`src/ingestion/loaders/`** — one loader per kind of file.

* `docling_loader.py` — PDFs and Word documents, including table grids
* `dxf_loader.py` — native CAD, via `ezdxf`
* `image_loader.py` — photographs and scans

The loader's job is only to turn bytes into a `RawDocument`: text blocks, tables,
page images. It makes no judgements about meaning.

The CAD path is the interesting one, because a DXF file is not a document — it is a
pile of lines, arcs and text floating in coordinate space. Turning that into
something readable takes four more modules:

| File | What it works out |
|---|---|
| `cad/spatial.py` | which label belongs to which piece of steel, by following leader lines |
| `cad/blocks.py` | what the repeated symbols mean, by grouping identical block definitions |
| `cad/schedules.py` | where the drawn tables are, and what their rows say |
| `cad/dxf_reader.py` | the raw read — the only file that imports `ezdxf` |

*Check it:* `python scripts/inspect_cad_associations.py <file.dxf>` prints what it
found and, just as usefully, what it could not resolve.

### Stop 4 — read the pictures, if we must

**`src/ingestion/ocr/`** and **`src/ingestion/layout/`**.

`OCRDetector` measures how much real text a page has. Pages that already have text
are left alone; only image-only pages go through OCR. This is a cost decision — OCR
is slow, and running it on a page that does not need it is pure waste.

### Stop 5 — should this be here at all?

**`src/ingestion/screening.py`**

Two questions, both answered without an LLM: *is this explicit content?* and *is this
an engineering document?* A cake recipe uploaded by mistake gets **quarantined**, not
deleted — stored, marked, kept out of search, with the reason recorded so a human can
disagree.

It sits here on purpose: after parsing, so there is text to judge, and **before**
enrichment and embedding, so a file that does not belong costs one regex pass instead
of an LLM call and a few hundred embeddings.

The threshold is deliberately generous. A false quarantine blocks real work; a false
pass just leaves an odd file sitting in a corpus where the retrieval filters and the
grounding guard already stop it being quoted as an authority. See
[§8](#8-five-rules-that-explain-most-decisions) for the principle.

*Check it:* `pytest tests/unit/ingestion/test_screening.py -v`

### Stop 6 — work out what it is about

**`src/ingestion/enrichers/`** and **`src/ingestion/extractors/`**

The enricher asks a small model for a summary, domain and tags. The extractor finds
steel entities — section designations, grades, bolt specs, piece marks — using about
forty regexes checked against a gazetteer in `src/ingestion/extractors/data/steel_sections.yaml`.

Regex before LLM, deliberately. `ISMB 300` is a fixed vocabulary, and a pattern that
either matches or does not is cheaper, faster and far easier to audit than a model
that is usually right.

### Stop 7 — cut it into pieces

**`src/ingestion/chunkers/hybrid_chunking_pipeline.py`**

You cannot embed a whole document usefully, so it gets cut into chunks. Four passes:

1. **structure** — split on real headings and sections
2. **semantic** — split further where the topic actually shifts
3. **parent/child** — keep a big chunk for context and small ones for precision
4. **validate** — throw away chunks that are OCR noise

Drawings take a different route (`ContentRoutedChunkingStrategy`), because a drawing
has almost no sentences and the semantic pass would do nothing useful. A schedule row
becomes its own chunk, with the header repeated so it still makes sense out of context.

### Stop 8 — turn text into numbers

**`src/ingestion/embedders/`** — an embedding is a list of numbers where similar
meanings land near each other. That is what makes "what holds up the roof" find a
chunk that says "purlin", with no shared words at all.

### Stop 9 — store it three times

| Store | Holds | Answers |
|---|---|---|
| **Postgres** | the truth — documents, chunks, jobs, audit | "what is this document?" |
| **Qdrant** | the embeddings | "what *means* something similar?" |
| **Elasticsearch** | the words | "what contains this exact term?" |

Three stores because the two search kinds genuinely fail differently. Vectors find
meaning but miss exact identifiers — ask for `CE-4` and a vector search happily
returns `CE-5`. Keyword search nails identifiers and misses paraphrases. Using both
and merging is the whole reason retrieval works here.

Writing to all three consistently is easy to get wrong, so both payloads are built by
one shared function in **`src/infrastructure/serialization/chunk_payload.py`**. Any
field added there lands in both stores by construction.

*Check it:*

```bash
curl http://localhost:16333/collections/documents          # Qdrant
curl http://localhost:19200/documents/_count               # Elasticsearch
```

---

## 5. Journey two — a question comes out

The question path is a **LangGraph state graph**, defined in
**`src/retrieval/graph/builder.py`**, with the node bodies in
`src/retrieval/graph/nodes.py`.

```
kill switch → cache → route ─┬─→ off-pipeline answer → done
                             └─→ retrieve → context → vision → generate → done
```

A graph rather than a chain of function calls because several stops can end the whole
thing early, and expressing "stop here" as an edge is far clearer than five levels of
early returns.

### Stop 1 — the kill switch

An operator can turn answering off without a deploy. A paused system says so plainly
instead of failing.

### Stop 2 — the cache

If this question was asked recently and the answer is still valid, return it. Free and
instant.

### Stop 3 — routing: does this even need the documents?

**`src/routing/router.py`** and **`src/routing/rules.py`**

Three layers, cheapest first:

1. **Rules** — deterministic patterns. "hi", "what can you do", "who is logged in",
   arithmetic, dates. Free, instant, no model call.
2. **Heuristics** — does this need current information the corpus cannot have?
3. **Classifier** — one small-model call for whatever is left.

Every question the rules catch is a vector search, a keyword search, a rerank, a
compression pass and a generation that never happened.

There is a guard here worth knowing about: `_keep_document_questions`. The classifier
does not know what the corpus contains, and it is confidently wrong in one specific
direction — asked *"what material is BP-1?"* it once answered, from its own knowledge,
that BP-1 is a biodegradable plastic. It is a 25mm steel base plate and the schedule
says so. So any question using drawing vocabulary is forced to retrieval, whatever the
classifier thinks. The override only ever moves *toward* retrieval, so it cannot
weaken grounding.

*Check it:* `pytest tests/unit/routing/ -v` — 158 tests, most of them guarding the
direction where being wrong is expensive.

### Stop 4 — retrieve

**`src/retrieval/`**

Vector search and keyword search run **in parallel**, then merge by Reciprocal Rank
Fusion (`fusers/`) — a chunk that both methods liked rises to the top. A reranker
(`rerankers/`) then reads the query and each candidate together and reorders them
properly.

Your clearance and project membership are pushed *into* both searches as filters, not
applied afterwards. Filtering after the fact means the database briefly handed you rows
you were not allowed to see.

### Stop 5 — build the context

**`src/retrieval/context/`** — compress the winners to fit the model's budget while
keeping citations attached, so every sentence can still be traced to a sheet.

### Stop 6 — vision, only when stuck

**`src/retrieval/vision/`**

If the answer is on the drawing but not in its text — an unlabelled symbol, a hatch
pattern — the system renders **just that region** and asks a vision model about it.

Four guards on this, because it is the most expensive path in the system:

* it is a **fallback**, never the first move
* only the region in question is rendered, never the whole drawing
* a low-confidence reading is discarded rather than reported
* **vision can never override a CAD fact** — an exact number from the file always wins
  over a picture of that number

### Stop 7 — generate, and check the answer

**`src/retrieval/answer/`**

The model writes the answer with `[1]`, `[2]` markers, and then those markers are
**validated against the chunks that were actually retrieved**. Two governance controls
apply here, both in `src/governance/policy.py`:

* **C-GOV-03** — nothing was retrieved → refuse
* **C-GOV-04** — an answer was produced but cited none of its sources → refuse

A refusal is a *result*, not an error. The UI renders it as one.

---

## 6. The things that say no

Five independent gates. Each does one job, and none substitutes for another.

| Gate | File | What it decides |
|---|---|---|
| **Authentication** | `src/auth/firebase.py` | are you who you say you are |
| **Role** | `src/governance/rbac.py` | may you do this at all |
| **Clearance** | `src/governance/policy.py` | how deep may you read |
| **Project membership** | `src/api/routes/projects.py` | which documents are in reach |
| **Grounding** | `src/governance/policy.py` | may this answer be shown |

Two details that are easy to get wrong and are done right here:

**Roles come from our database, never from the token.** Firebase proves *who* you are.
What you may do is read from Postgres on every request, so a forged or stale token
cannot assert privilege.

**A missing document is a 404, not a 403.** Telling someone "you are not allowed to
see this" confirms it exists, which is itself a disclosure.

Everything privileged is written to an append-only audit log
(`src/governance/audit.py`): who, when, what, and the before/after state.

*Check it:* `pytest tests/unit/governance/ -v`, and
`python scripts/check_governance.py` — which fails the build if the risk register
drifts from the code.

---

## 7. The front end

React 19 + Vite + TypeScript, in `frontend/`.

```
frontend/src/
├── pages/           one file per screen
├── components/
│   ├── ui/          generic buttons, dialogs, inputs
│   ├── domain/      things that only make sense here — citations, sheet viewer
│   └── layout/      the shell and navigation
├── api/             the typed client
├── lib/             auth, theme, speech, helpers
└── test/            shared test helpers
```

**Start with `App.tsx`** — every route in one screen of code, each wrapped in the role
it requires. Then `components/layout/app-shell.tsx` for the frame around them.

The API layer is worth understanding as a shape: `api/surface.ts` declares the
interface, `api/real.ts` implements it against the backend, and `api/mock/` implements
the *same* interface with fixtures. One environment variable switches between them, so
the whole UI can be developed and tested with no backend at all.

**A caution learned the hard way.** The types in `api/types.ts` are written by hand, so
they can lie. Three separate bugs this year were a field typed as always-present that
the server never sent — each one crashed a page. `pages/api-contract.test.tsx` now
tests pages against payloads copied verbatim from the running API, which is the only
version of this that catches drift.

### Testing, in three layers

| Layer | Tool | Catches |
|---|---|---|
| `src/**/*.test.tsx` | Vitest + Testing Library | logic, states, wiring |
| `e2e/*.layout.spec.ts` | Playwright | real geometry — sizes, alignment, spacing |
| `e2e/*.visual.spec.ts` | Playwright screenshots | colour, overlap, "looks wrong" |

The middle layer exists because jsdom has **no layout engine**. A real bug shipped past
unit tests, typecheck and build: the collapsed sidebar's links were wrapped in a
tooltip whose `asChild` merges `className` by string concatenation, so it *stringified*
the className function instead of calling it. Every class was inert. The icons rendered
as bare 16px glyphs with no hit target and no active state. Only a browser reporting
`height: 16px` where 36 was intended could see it.

```bash
npm test              # Vitest
npm run test:layout   # Playwright geometry
npm run test:visual   # Playwright screenshots
```

---

## 8. Five rules that explain most decisions

If a choice in this codebase looks odd, it is usually one of these.

**1. "I don't know" beats a confident wrong answer.** Unresolved annotations stay
unresolved. Low-confidence vision readings are discarded. A table it cannot read is a
missing table, not a guessed one. A fabricated engineering schedule could get someone
hurt; a missing one is an inconvenience.

**2. "Can't tell" means allow.** The document screen quarantines only when it is
confident. A short document is passed rather than judged, because a ratio over eighty
words is noise. A false quarantine blocks real work and the user gives up; a false pass
leaves an odd file that four other controls still stop being quoted.

**3. Deterministic first, LLM last.** Regex, geometry and lookup tables come before any
model call. Models are used where they are genuinely better — summarising prose,
describing a picture — and nowhere else. Cheaper, faster, reproducible, auditable.

**4. Move toward retrieval, never away.** Every routing override can add a search but
never remove one. Sending a general question to retrieval wastes one search and still
answers correctly, because an empty retrieval refuses. Sending a document question to
model knowledge produces a fluent uncited paragraph about somebody else's drawing.

**5. Nothing is deleted; things are marked.** Quarantine, supersede, soft-delete. A
reviewer can release a quarantined file in seconds and cannot recover a refused upload.

---

## 9. Why each tool, and what we said no to

| Tool | Job | Why this one |
|---|---|---|
| **FastAPI** | HTTP | async-native, and generates the `/docs` page from the code |
| **Postgres** | truth | relational data with real constraints; boring in the best way |
| **Qdrant** | vectors | filters *inside* the vector search, so clearance is a pre-filter |
| **Elasticsearch** | keywords | BM25 finds the exact `CE-4` that vectors blur |
| **Redis + arq** | queue | arq is async-native and reuses Redis; Celery is thread-oriented and heavier |
| **ezdxf** | CAD | MIT-licensed, pure Python, the only file that imports it |
| **LangGraph** | query flow | the flow has genuine branches and early exits |
| **React + Vite** | UI | fast builds, and the ecosystem the team knows |
| **Playwright** | browser tests | the only thing that can see layout |

**Deliberately not used:**

* **spaCy** — a 200MB install to do what 40 regexes and a YAML file do more auditably.
* **PyMuPDF** — AGPL, which is a real problem for a commercial product. `pypdfium2` instead.
* **YOLO / Detectron2** — AGPL and unbuildable-from-wheels respectively, and a custom
  detector needs 500–2000 labelled drawings that do not exist yet.
* **A vision model on every drawing** — the cost is enormous and the deterministic path
  is more accurate. Vision is a fallback.
* **Presigned URLs for files** — they bypass our own clearance checks entirely. Bytes
  are served through the API so the same rules apply.

---

## 10. How to check anything

```bash
# everything, in one go
make ci

# the pieces
pytest tests/unit -q                  # 1,490 backend tests
cd frontend && npm test               # 101 frontend tests
cd frontend && npm run test:layout    # browser geometry
ruff check src tests                  # lint
mypy                                  # types (scope lives in pyproject.toml)
python scripts/check_governance.py    # docs vs code consistency
```

**Reading the system while it runs:**

```bash
docker compose logs -f api worker     # structured logs
curl localhost:18000/api/v1/metrics   # Prometheus metrics
```

Every request carries a trace id, returned in the `X-Trace-Id` header and shown in the
UI. Grepping the logs for it gives you the whole life of that one request.

---

## 11. When something breaks, look here

| Symptom | Start at |
|---|---|
| Upload stuck in `processing` | `docker compose logs worker`, then `src/jobs/worker.py` |
| Document `indexed` with 0 chunks | `src/ingestion/chunkers/chunk_validator.py` |
| Document went to `quarantined` | its `error_message`; rules in `src/ingestion/screening.py` |
| Answer refused unexpectedly | trace id in logs → `C-GOV-03` vs `C-GOV-04` in `src/governance/policy.py` |
| Answer is fluent but uncited | `src/retrieval/answer/` — citation validation |
| A drawing question got a textbook answer | `src/routing/rules.py` — vocabulary lists |
| Search finds nothing for an exact mark | Elasticsearch side; `chunk_payload.py` for what got indexed |
| A page renders blank | browser console — the error boundary in `components/domain/error-boundary.tsx` shows the message |
| A page looks wrong but works | `npm run test:layout` — it measures real boxes |

---

## Where to go next

* **`docs/HOW_IT_WORKS.md`** — the original deep dive on the RAG core, with worked
  examples and real costs per request.
* **`docs/architecture/`** — fifteen reference documents: schema, API, risk, cost.
* **`docs/governance/`** — the risk register, data card and model card. The register is
  machine-checked against the code by `scripts/check_governance.py`, so it cannot
  quietly go stale.
* **`tests/unit/`** — genuinely the best documentation in the repo. Each test says what
  the behaviour is *and* why it matters.
