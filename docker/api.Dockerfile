FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked

# --- Runtime ---
FROM python:3.12-slim

WORKDIR /app

# curl: container healthcheck. The rest: opencv-python (pulled in transitively
# by docling-ibm-models' TableFormer model) dynamically links against these at
# import time even though it never opens a window.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd -r raguser && useradd -r -g raguser -m -d /home/raguser raguser \
    && chown raguser:raguser /home/raguser

COPY --from=builder --chown=raguser:raguser /app/.venv /app/.venv
COPY --chown=raguser:raguser src/ ./src/
COPY --chown=raguser:raguser alembic.ini ./

RUN mkdir -p /app/uploads && chown raguser:raguser /app/uploads

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/raguser

USER raguser

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
