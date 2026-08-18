.PHONY: help dev build test lint type-check migrate seed clean logs stop \
        governance-check eval-gate retention-dry-run

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
	@echo ""
	@echo "Governance (GM3):"
	@echo "  make governance-check    Validate risk register vs metrics, controls, alerts"
	@echo "  make eval-gate           Fail if quality is below the policy floors"
	@echo "  make retention-dry-run   List documents past their retention deadline"

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

governance-check:
	uv run python scripts/check_governance.py

eval-gate:
	uv run python scripts/eval_gate.py --from-api $(or $(API),http://localhost:8000) --dataset $(or $(DATASET),golden_set_v1)

retention-dry-run:
	curl -s -X POST "$(or $(API),http://localhost:8000)/api/v1/governance/retention/run?dry_run=true" -H "X-User-Id: 00000000-0000-0000-0000-000000000001"

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

ci: lint-check type-check test governance-check

.DEFAULT_GOAL := help
