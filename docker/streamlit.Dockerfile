FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked

FROM python:3.12-slim

WORKDIR /app

RUN groupadd -r raguser && useradd -r -g raguser -m -d /home/raguser raguser \
    && chown raguser:raguser /home/raguser

COPY --from=builder --chown=raguser:raguser /app/.venv /app/.venv
COPY --chown=raguser:raguser src/ui/ ./src/ui/
COPY --chown=raguser:raguser src/config.py ./src/
COPY --chown=raguser:raguser src/__init__.py ./src/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/raguser

USER raguser

EXPOSE 8501

CMD ["streamlit", "run", "src/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
