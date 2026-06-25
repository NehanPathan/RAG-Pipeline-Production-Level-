.PHONY: help dev build test lint type-check migrate seed clean logs stop

help:
	@echo "Enterprise Agentic RAG Platform"
	@echo ""
	@echo "Usage:"
	@echo "  make dev          Start all services in development mode"
	@echo "  make build        Build Docker images"
	@echo "  make test         Run test suite with coverage"
	@echo "  make lint         Run ruff linter"
	@echo "  make type-check   Run mypy type checker"
	@echo "  make migrate      Run Alembic database migrations"
	@echo "  make seed         Seed evaluation dataset"
	@echo "  make logs         Tail Docker Compose logs"
	@echo "  make stop         Stop all services"
	@echo "  make clean        Remove containers, volumes, and cache"
	@echo "  make install      Install Python dependencies with uv"

install:
	uv sync --all-extras

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

build:
	docker compose build

stop:
	docker compose down

clean:
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

logs:
	docker compose logs -f

migrate:
	uv run alembic upgrade head

migrate-create:
	uv run alembic revision --autogenerate -m "$(MSG)"

seed:
	uv run python scripts/seed_eval_dataset.py

create-admin:
	uv run python scripts/create_admin.py

lint:
	uv run ruff check src tests --fix

lint-check:
	uv run ruff check src tests

format:
	uv run ruff format src tests

type-check:
	uv run mypy src

test:
	uv run pytest tests/ -v

test-unit:
	uv run pytest tests/unit/ -v

test-integration:
	uv run pytest tests/integration/ -v

test-e2e:
	uv run pytest tests/e2e/ -v

test-cov:
	uv run pytest tests/ --cov=src --cov-report=html --cov-report=term-missing

api:
	uv run uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

ui:
	uv run streamlit run src/ui/app.py --server.port 8501

ci: lint-check type-check test

.DEFAULT_GOAL := help
