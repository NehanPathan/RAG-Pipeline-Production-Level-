# RAG Pipeline — Production Level

An Enterprise-grade Agentic RAG (Retrieval-Augmented Generation) platform built with FastAPI, Postgres, Redis, Qdrant, and Elasticsearch — designed with a clean, layered architecture (domain / application / infrastructure) for production deployment.

## Features

- Document ingestion pipeline (loaders, cleaners, chunkers, embedders, enrichers)
- Hybrid retrieval combining vector search (Qdrant) and keyword search (Elasticsearch BM25) with RRF fusion
- Reranking (BGE / Cohere) and context compression with citation preservation
- Semantic caching, token budget management, and streaming answer generation
- Pluggable LLM provider registry
- Observability: structured logging, Prometheus metrics, Langfuse tracing
- Offline & online evaluation pipelines
- Dockerized multi-service stack (API, Streamlit UI, Postgres, Redis, Qdrant, Elasticsearch)

## Architecture

See [docs/architecture](docs/architecture) for detailed design docs covering system architecture, component diagrams, sequence diagrams, data flow, database schema, API design, risk analysis, cost analysis, and the build roadmap.

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

This starts the API (`http://localhost:8000`), Streamlit UI (`http://localhost:8501`), Postgres, Redis, Qdrant, and Elasticsearch.

### Run locally (without Docker)

```bash
make migrate   # apply database migrations
make api        # start the FastAPI server
make ui         # start the Streamlit UI
```

## Development

```bash
make lint         # ruff lint
make format        # ruff format
make type-check    # mypy
make test          # run full test suite
make test-cov       # run tests with coverage report
```

## Project Structure

```
src/
├── api/            # FastAPI routes, middleware, dependencies
├── application/    # Use cases, DTOs
├── domain/         # Entities, value objects, repository interfaces
├── infrastructure/ # Database, vector store, search implementations
├── ingestion/      # Document loaders, chunkers, embedders, enrichers
├── retrieval/       # Hybrid retrieval, reranking, answer generation agents
├── llm/             # LLM provider registry and prompts
├── monitoring/      # Logging, metrics, tracing
└── evaluation/      # Offline & online evaluation pipelines
```

## Environment Variables

All required environment variables are documented in [.env.example](.env.example). Copy it to `.env` and fill in your own credentials before running the project — `.env` is git-ignored and must never be committed.

## License

This project is for educational and portfolio purposes.
