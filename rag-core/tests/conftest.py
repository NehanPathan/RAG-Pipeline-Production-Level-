import asyncio
import os
import uuid

# A placeholder credential, set at conftest import -- before anything builds a
# `Settings` -- so the suite does not depend on the ambient environment.
#
# `Settings.openai_api_key` defaults to "", so the LLM gateway passes
# `api_key=None` and the OpenAI client falls back to reading OPENAI_API_KEY
# itself, raising at *construction* when it is unset. Any test that builds a
# real pipeline (tests/unit/api/test_dependencies.py) therefore passed only on
# a machine that happened to have a live key exported, and failed on a clean
# CI runner with "No usable LLM provider for role 'small'".
#
# The value is deliberately not a real key: nothing here calls the API, so a
# syntactically-valid placeholder is all the constructor needs, and a test
# that did start making network calls would fail loudly rather than quietly
# spending someone's credit.
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import Document, DocumentStatus
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock


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
