FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked

FROM python:3.12-slim

WORKDIR /app

RUN groupadd -r raguser && useradd -r -g raguser raguser

COPY --from=builder /app/.venv /app/.venv
COPY src/ui/ ./src/ui/
COPY src/config.py ./src/
COPY src/__init__.py ./src/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN chown -R raguser:raguser /app
USER raguser

EXPOSE 8501

CMD ["streamlit", "run", "src/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
