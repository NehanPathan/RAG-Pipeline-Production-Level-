import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

# Unit tests must not contact anything outside this process.
#
# `Settings` is a pydantic-settings model that reads `.env`, and a real
# deployment's `.env` carries live Langfuse cloud credentials and an OTLP
# collector endpoint. Without this, importing the app in a unit test wires up
# real exporters: the suite makes network calls to cloud.langfuse.com, and the
# BatchSpanProcessor blocks on retrying failed exports at interpreter
# shutdown -- which presents as the whole suite hanging after the last test,
# with no failure and no clue as to why.
#
# Environment variables outrank the `.env` file in pydantic-settings, so
# blanking them here (at conftest import, before any Settings is built) is
# what makes the suite hermetic. Assignment, not setdefault: the point is to
# override whatever `.env` says.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["OTEL_SDK_DISABLED"] = "true"

import pytest  # noqa: E402

from src.domain.entities.document import Document, DocumentStatus  # noqa: E402
from src.ingestion.chunkers.parent_child_chunker import (  # noqa: E402
    ChunkingConfig,
    ParentChildChunker,
)
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock  # noqa: E402
from pathlib import Path  # noqa: E402


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def sample_user_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def sample_document(sample_user_id) -> Document:
    return Document(
        file_name="test_policy.pdf",
        file_type="pdf",
        file_size_bytes=1024 * 50,
        user_id=sample_user_id,
        status=DocumentStatus.PENDING,
    )


@pytest.fixture
def sample_raw_document() -> RawDocument:
    return RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=[
            TextBlock(text="This is a sample policy document about refund procedures.", page_number=1),
            TextBlock(text="Customers may request refunds within 30 days of purchase.", page_number=1),
            TextBlock(text="Refund requests must be submitted through the customer portal.", page_number=2),
        ],
        tables=[
            TableBlock(
                markdown="| Reason | Timeline |\n|---|---|\n| Defective | 30 days |\n| Changed mind | 14 days |",
                page_number=2,
                row_count=3,
                col_count=2,
            )
        ],
        page_count=2,
        word_count=45,
        loader_name="docling",
    )


@pytest.fixture
def chunker() -> ParentChildChunker:
    config = ChunkingConfig(
        parent_chunk_size=256,
        child_chunk_size=64,
        overlap=8,
    )
    return ParentChildChunker(config=config)


@pytest.fixture
def mock_document_repo():
    repo = AsyncMock()
    repo.save = AsyncMock()
    repo.update = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_chunk_repo():
    repo = AsyncMock()
    repo.save_batch = AsyncMock(side_effect=lambda chunks: chunks)
    return repo


@pytest.fixture
def mock_vector_repo():
    repo = AsyncMock()
    repo.create_collection_if_not_exists = AsyncMock()
    repo.upsert_batch = AsyncMock()
    return repo


@pytest.fixture
def mock_search_repo():
    repo = AsyncMock()
    repo.create_index_if_not_exists = AsyncMock()
    repo.index_batch = AsyncMock()
    return repo


@pytest.fixture
def mock_embedding_provider():
    provider = AsyncMock()
    provider.model_id = "text-embedding-3-large"
    provider.dimensions = 3072
    provider.embed_texts = AsyncMock(
        side_effect=lambda texts: [[0.1] * 3072 for _ in texts]
    )
    provider.embed_query = AsyncMock(return_value=[0.1] * 3072)
    return provider


@pytest.fixture
def mock_llm_provider():
    provider = AsyncMock()
    provider.model_id = "mock-llm"
    provider.complete = AsyncMock(return_value="{}")

    async def _default_stream(*args, **kwargs):
        for token in ("mock", " ", "response"):
            yield token

    provider.stream = _default_stream
    return provider


@pytest.fixture
def mock_llm_enricher():
    from src.domain.entities.document import DocumentMetadata
    enricher = AsyncMock()
    enricher.enrich = AsyncMock(
        return_value=DocumentMetadata(
            summary="A policy document about refunds.",
            tags=["refund", "policy", "returns"],
            domain="Operations",
            language="en",
        )
    )
    return enricher
