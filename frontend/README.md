# Girder — Steel Document Intelligence frontend

React + TypeScript + Vite + Tailwind v4 + shadcn/ui, built against the FastAPI
service in `../src/api`.

```bash
npm install
npm run dev      # http://localhost:5173, bundled sample corpus
npm run build    # typecheck + production bundle into dist/
```

## Running against the real backend

The app ships with an in-memory sample corpus so it runs standalone. One
environment variable switches it to the live service:

```bash
cp .env.example .env.local
# VITE_USE_MOCKS=false
```

`src/api/index.ts` picks between `mockApi` and `realApi`; both implement the
same `ApiSurface` interface (`src/api/surface.ts`), so no page or hook knows
which one it is talking to. The dev server proxies `/api` to
`http://localhost:8000` (override with `VITE_API_TARGET`).

Auth mirrors `resolve_principal()`: a Firebase ID token as
`Authorization: Bearer`, or — with Firebase disabled — the `X-User-Id`
development header. The header path is labelled **Unverified session** in the
top bar, because "signed in" and "authenticated" are not the same claim.

## Docker

The frontend is a service in the root `docker-compose.yml`:

```bash
cd ..
docker compose up -d frontend      # http://localhost:15173
docker compose build frontend      # after a source change
```

`docker/frontend.Dockerfile` builds with Node and serves the result from
`nginxinc/nginx-unprivileged` — the Node toolchain never reaches the runtime
image. nginx also reverse-proxies `/api` onto the `api` container, so the
browser sees a single origin: the app reads the `X-Trace-Id` response header,
which browsers hide cross-origin unless explicitly exposed, and this lets the
API keep an empty CORS allow-list outside development. `proxy_buffering` is
off on that location so the SSE answer stream is not held back until complete.

`VITE_USE_MOCKS` is a **build arg**, not runtime config — Vite inlines
`import.meta.env` at build time. Compose passes `false`, which is the only
correct value for a deployed container: the sample corpus would otherwise
render a convincing product full of drawings that do not exist.

## Design direction — "Drafting Table"

The visual language borrows from the artefacts the product is about: drafting
vellum, blueprint grids, drawing title blocks, mill-finished steel.

- **Hairlines, not shadows.** Structure is drawn with 1px borders. Elevation is
  reserved for things that genuinely float — popovers, dialogs, the palette.
- **Tight radii.** 6px on panels, 4px on controls, 0 on data cells.
- **Numbers are data.** Every identifier — drawing number, revision label,
  piece mark, score, trace id — is mono with tabular figures, so columns align
  and a transposed digit is visible.
- **One signal colour.** A cool graphite neutral ramp carrying hot-rolled
  amber. Blueprint blue is secondary, for technical/provenance information.
  Classification owns a separate fixed four-step ramp that is never reused, so
  a colour on screen means exactly one thing. See `/legend` in the app.

Tokens live in `src/index.css` under `@theme inline`, with a full light and
dark palette. Theme follows the system unless the user picks one.

## Layout

```
src/
  api/            wire types, HTTP client, ApiSurface, real + mock implementations
    mock/         the sample corpus and its handlers
  components/
    ui/           shadcn/ui primitives
    domain/       the product's vocabulary: badges, sheet viewer, answer rendering
    layout/       shell, sidebar, command palette, page container
  lib/            auth, theme, formatting
  pages/          one file per route
```

## Pages

| Route | What it does |
|---|---|
| `/login` | Sign-in; verified and development identity modes |
| `/` | Overview — corpus health, live ingestion, attention items |
| `/ask` | Streaming RAG chat, citations wired to the sheet viewer, feedback |
| `/search` | Faceted keyword + semantic search over chunks |
| `/projects`, `/projects/:id` | Project register, drawings, documents, access |
| `/drawings`, `/drawings/:id` | Drawing register and revision history with compare |
| `/documents`, `/documents/:id` | Document library; viewer, chunks, entities, intelligence, jobs |
| `/ingest`, `/ingest/queue` | Upload with per-file metadata; stage-by-stage job queue |
| `/ops/monitoring` | Service health, throughput, latency, providers, plugins |
| `/ops/quality` | Release gate, evaluation runs, trend, user feedback |
| `/ops/inspector` | Vector / BM25 / fusion / rerank, stage by stage |
| `/ops/governance` | Policy, kill switches, risk register, audit trail, retention |
| `/ops/access` | Roles, clearance matrix, project rosters |
| `/ops/settings` | Models, retrieval parameters, chunking |
| `/legend` | What every badge, colour and score means |

Route access mirrors `require_role` — an allow-list, not a rank comparison.

## Domain rules the UI enforces

These are not decoration; each one exists because getting it wrong is
expensive on a real job.

- **A drawing is an identity that outlives its revisions.** Rev A/B/C are three
  documents of one drawing. Superseded revisions are struck through, stamped in
  the viewer, and excluded from search and answers unless explicitly included.
- **Provenance is never flattened.** A dimension from a DXF is a CAD value; on
  a plotted sheet it is a rendered string; on a scan it is OCR's reading. Only
  `cad_native` is labelled exact.
- **404 does not mean missing.** The API returns 404 rather than 403 for a
  document above your clearance, so the UI says "does not exist, or your
  clearance does not cover it" instead of promising it is gone.
- **Highlight precision is stated honestly.** `block` regions get a tight box,
  `section` regions a soft dashed one — the semantic chunker has no
  sentence-to-box map, and a confident rectangle around the wrong sentence is
  worse than an admitted approximation.
- **A refusal is a result.** When nothing clears the grounding threshold the
  answer panel says so rather than showing an error.

## Endpoints not yet on the backend

Marked `PLANNED` in `src/api/types.ts` and throwing a 501 from `realApi`:
projects, drawings, revisions, a global job queue, a JSON metrics summary, and
a user list. The domain entities and repositories exist
(`src/domain/entities/project.py`, `src/domain/repositories/project_repository.py`)
and the design is fixed in `docs/architecture/14_steel_domain_design.md`; only
the HTTP surface is missing. Pages that use them render a "not wired up yet"
state naming the endpoint, so nothing silently fabricates data.

## Notes

- Charts are lazy-loaded — someone who only uses Ask never downloads Recharts.
- Bundle splits by change rate as well as size, so a deploy invalidates as
  little of the user's cache as possible.
- No external network requests at runtime: no CDN fonts, no remote images.
