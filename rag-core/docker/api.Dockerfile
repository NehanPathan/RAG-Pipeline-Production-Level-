FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked

# --- Runtime ---
FROM python:3.12-slim

WORKDIR /app

# curl: container healthcheck. opencv deps (libgl1..libfontconfig1): pulled in
# transitively by docling-ibm-models' TableFormer model, which dynamically
# links against these at import time even though it never opens a window.
# tesseract-ocr: the actual OCR binary `pytesseract`/`unstructured_pytesseract`
# shells out to -- OCR_PROVIDER=tesseract (the default) silently had nothing
# to run against until this was added (found via a live end-to-end OCR test,
# see docs/architecture/12_phase4a_design_review.md). poppler-utils:
# `pdf2image`'s `convert_from_path` (used to render PDF pages to images for
# Tesseract) shells out to poppler's `pdftoppm`, also missing until now.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    libfontconfig1 \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd -r raguser && useradd -r -g raguser -m -d /home/raguser raguser \
    && chown raguser:raguser /home/raguser

COPY --from=builder --chown=raguser:raguser /app/.venv /app/.venv
COPY --chown=raguser:raguser src/ ./src/
COPY --chown=raguser:raguser alembic.ini ./
# Runtime configuration, not documentation -- src/governance/risk_register.py
# reads this to serve /governance/risks and /governance/status.
COPY --chown=raguser:raguser docs/governance/risk_register.yaml ./docs/governance/risk_register.yaml

RUN mkdir -p /app/uploads && chown raguser:raguser /app/uploads

# Create the model-cache directory *in the image* and give it to raguser.
# A named volume mounted over a path that does not exist in the image is
# created root-owned, and this container runs as a non-root user -- which
# fails at startup with EACCES on the first HuggingFace download. Docker
# seeds a fresh named volume from the image's directory (contents and
# ownership), so creating it here is what makes the mount writable.
RUN mkdir -p /home/raguser/.cache/huggingface \
    && chown -R raguser:raguser /home/raguser/.cache

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/raguser

USER raguser

EXPOSE 8000

# --workers 1: each worker independently loads its own copy of every ML
# model (BGE-M3 chunking embedder at startup, plus Docling's layout/table/
# OCR models on first PDF use). With 2 workers alongside Postgres/
# Elasticsearch/Qdrant/Redis/Streamlit sharing one Docker Desktop memory
# budget, an image-heavy PDF's one-time Docling model load could exceed
# available memory and get a worker OOM-killed mid-ingestion (found during
# Phase 4A verification -- see docs/architecture/12_phase4a_design_review.md).
#
# This relies on DoclingLoader/UnstructuredLoader running their CPU-bound
# parsing via asyncio.to_thread (also fixed during Phase 4A verification) --
# without that, a single worker would fully freeze on every PDF/DOCX upload
# instead of just serializing ingestion work behind other requests.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
