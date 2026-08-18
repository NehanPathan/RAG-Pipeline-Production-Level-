# RAG Pipeline — Production Level

An Enterprise-grade Agentic RAG (Retrieval-Augmented Generation) platform built with FastAPI, Postgres, Redis, Qdrant, and Elasticsearch — designed with a clean, layered architecture (domain / application / infrastructure) for production deployment.

## Features

- **Intelligent query router** — classifies each query and answers greetings, arithmetic and date questions with *zero* model calls, translation and general knowledge with one, and only sends document questions through the full retrieval pipeline
- **Tool layer** — calculator (AST-evaluated, never `eval`), date/time, translation, web search and read-only SQL, each a drop-in plugin
- **AI Gateway** — one path for every LLM call, with an ordered provider fallback chain (OpenAI, Anthropic, Ollama, OpenRouter, Azure), cost accounting and the approved-model check
- **Workflow engine** — pipelines as node graphs rather than nested conditionals, each node independently traced and testable
- Document ingestion pipeline (loaders, cleaners, chunkers, embedders, enrichers)
- Hybrid retrieval combining vector search (Qdrant) and keyword search (Elasticsearch BM25) with RRF fusion
- Reranking (BGE / Cohere) and context compression with citation preservation
- Semantic caching, token budget management, and streaming answer generation
- **Firebase authentication** — verified identity feeding RBAC, clearance and audit attribution
- **PII detection and redaction** on retrieved context and generated answers
- **Feature flags** — per-environment capability toggles plus database-backed kill switches, changeable without a redeploy
- **Plugin architecture** — one generic registry behind LLM providers, tools, PII detectors, embedders and rerankers
- Observability: structured logging, Prometheus metrics, OpenTelemetry + Langfuse tracing, Grafana dashboards
- Offline & online evaluation pipelines with LLM-judge metrics
- **AI governance (NIST AI RMF: Govern / Map / Measure / Manage)** — declarative policy, data classification enforced at retrieval time, append-only audit trail, kill switches, and a CI gate that fails the build when quality drops below policy
- Dockerized multi-service stack (API, Streamlit UI, Postgres, Redis, Qdrant, Elasticsearch, OTel Collector, Prometheus, Grafana)

## Start here

**[docs/WHAT_WE_BUILT.md](docs/WHAT_WE_BUILT.md)** — a plain-English walkthrough
of every part of the system: what it does, why it's built that way, and how to
explain it to someone else. No assumed background. Read this before the
architecture docs.

**[docs/SECURITY.md](docs/SECURITY.md)** — every security control in one place,
for both FastAPI and Streamlit: the threats, the five layers, the code that
implements each, what is *not* protected, and a pre-deployment checklist.

## Architecture

See [docs/architecture](docs/architecture) for detailed design docs covering system architecture, component diagrams, sequence diagrams, data flow, database schema, API design, risk analysis, cost analysis, and the build roadmap.

### Query flow

```
                        query
                          │
              ┌───────────▼────────────┐
              │  Query Router          │  rules → heuristics → classifier
              └───────────┬────────────┘
                          │
  ┌────────┬────────┬─────┴────┬─────────┬─────────┬────────┬──────┐
  ▼        ▼        ▼          ▼         ▼         ▼        ▼      ▼
greeting calc   datetime  translation  llm      web      sql    RAG
  │        │        │          │     knowledge  search    │      │
 0 LLM   0 LLM    0 LLM     1 small   1 large  external  read  hybrid
 calls   calls    calls      call      call     (opt-in) only  retrieval
```

Rules run before the classifier because a deterministic match is free,
instant and reproducible. Ambiguity resolves to RAG: answering from the wrong
source is a correctness failure, while a needless retrieval is only a cost.

`rag_retrieval_skipped_total` is the metric that shows the router paying for
itself — every increment is an embedding call, two searches, a rerank and a
compression call that did not happen.

Full design: [13_query_routing_and_tools.md](docs/architecture/13_query_routing_and_tools.md).

## Governance (GM3)

The platform implements the four functions of the NIST AI Risk Management
Framework. See [docs/governance/GM3_FRAMEWORK.md](docs/governance/GM3_FRAMEWORK.md)
for the full control mapping.

| Function | What it does here |
|---|---|
| **Govern** | One frozen `AIPolicy` read by the request path, the eval runner and the CI gate; model allow-lists checked at startup; RBAC on every mutating route; append-only audit trail |
| **Map** | Four-level data classification inherited by every chunk and pushed into the Qdrant/Elasticsearch filters *before* search runs, plus an in-process guard behind it; a machine-readable [risk register](docs/governance/risk_register.yaml) binding each risk to the metric that watches it |
| **Measure** | 21 Prometheus metrics, per-stage traces, offline evaluation on a golden set and online judging of sampled live traffic using the same prompts |
| **Manage** | Database-backed kill switches (no redeploy), retention enforcement across all four stores, and a feedback loop that turns a thumbs-down into a permanent regression case |

The load-bearing part is that governance is **enforced, not documented**:

```bash
make governance-check   # fails if the risk register drifts from the code
make eval-gate          # fails if quality is below the policy floors
```

`check_governance.py` fails CI when a risk names a metric nobody records, a
control names a file that no longer exists, a declared metric has no recording
call site, or a threshold has no alert rule.

Governance console: Streamlit → **Governance**. Dashboards: Grafana at
`http://localhost:3000` → *Governance* folder.

### Authentication

Firebase ID tokens are cryptographically verified before any identity is
trusted, which is what makes RBAC, clearance filtering and audit attribution
mean something. New users are provisioned at the least-privileged role —
authenticating proves who someone is, not what they may read.

```env
FIREBASE_ENABLED=true
FIREBASE_SERVICE_ACCOUNT=config/firebase-service-account.json
FIREBASE_ALLOWED_DOMAINS=yourcompany.com
```

> **`FIREBASE_ENABLED=false` is development only.** In that mode identity
> comes from an unverified `X-User-Id` header and proves nothing.
> `Principal.auth_provider` records which mode produced each identity, so
> development audit entries are not mistaken for verified attribution. Any
> deployment reachable by untrusted callers must enable it.

Never commit the service-account JSON — it signs tokens for every user. See
[config/README.md](config/README.md) and
[AUTHENTICATION.md](docs/governance/AUTHENTICATION.md).

Also see the [model card](docs/governance/model_card.md) and
[data card](docs/governance/data_card.md).

## Tech Stack

- **API**: FastAPI, Uvicorn
- **Database**: PostgreSQL + SQLAlchemy (async) + Alembic
- **Cache**: Redis
- **Vector Store**: Qdrant
- **Search**: Elasticsearch
- **UI**: Streamlit
- **Package Management**: uv

## Getting Started

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- [uv](https://github.com/astral-sh/uv)

### Setup

```bash
# Clone the repo
git clone https://github.com/NehanPathan/RAG-Pipeline-Production-Level-.git
cd RAG-Pipeline-Production-Level-

# Copy environment template and fill in your own values
cp .env.example .env

# Install dependencies
make install
```

### Run with Docker Compose

```bash
make build
make dev
```

This starts the API (`http://localhost:8000`), Streamlit UI (`http://localhost:8501`), Postgres, Redis, Qdrant, Elasticsearch, the OpenTelemetry Collector (`:4317`), Prometheus (`http://localhost:9090`) and Grafana (`http://localhost:3000`).

### Run locally (without Docker)

```bash
make migrate   # apply database migrations
make api        # start the FastAPI server
make ui         # start the Streamlit UI
```

## Development

```bash
make lint              # ruff lint
make format            # ruff format
make type-check        # mypy
make test              # run full test suite
make test-cov          # run tests with coverage report
make governance-check  # validate risk register vs metrics, controls, alerts
make eval-gate         # fail if quality is below the policy floors
make ci                # lint + types + tests + governance
```

## Project Structure

```
src/
├── api/            # FastAPI routes, trace-context middleware, dependencies
├── application/    # Use cases, DTOs
├── auth/           # Firebase token verification and user provisioning
├── domain/         # Entities, value objects, repository interfaces
├── governance/     # Policy, RBAC, audit, PII, flags, kill switches, retention
├── infrastructure/ # Database, vector store, search implementations
├── ingestion/      # Document loaders, chunkers, embedders, enrichers
├── llm/            # AI gateway, provider registry, pricing, prompts
├── monitoring/     # Logging, metrics, tracing
├── plugins/        # Generic plugin registry + discovery
├── retrieval/      # Hybrid retrieval, reranking, answer generation agents
├── routing/        # Query router: rules, heuristics, classifier
├── tools/          # Calculator, datetime, translation, web search, SQL
├── workflow/       # Node-graph execution engine
└── evaluation/     # Offline (golden set) & online (live traffic) evaluation
```

## Environment Variables

All required environment variables are documented in [.env.example](.env.example). Copy it to `.env` and fill in your own credentials before running the project — `.env` is git-ignored and must never be committed.

## License

This project is for educational and portfolio purposes.
