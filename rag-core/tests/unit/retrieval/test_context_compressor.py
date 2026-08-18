import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.context_compressor import ContextCompressor


def _reranked(content, chunk_id=None):
    chunk = DocumentChunk(
        id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
    )
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    return RerankedChunk(fused=fused, rerank_score=0.9)


@pytest.fixture
def compressor(mock_llm_provider):
    return ContextCompressor(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_compress_returns_llm_extracted_content(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="relevant excerpt only")
    chunks = [_reranked("full original chunk content")]

    result = await compressor.compress("query", chunks)

    assert len(result) == 1
    assert result[0].compressed_content == "relevant excerpt only"


@pytest.mark.asyncio
async def test_compress_drops_chunk_when_llm_returns_none(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="NONE")
    chunks = [_reranked("irrelevant content")]

    result = await compressor.compress("query", chunks)

    assert result == []


@pytest.mark.asyncio
async def test_compress_falls_back_to_original_content_on_llm_exception(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    chunks = [_reranked("original content")]

    result = await compressor.compress("query", chunks)

    assert len(result) == 1
    assert result[0].compressed_content == "original content"


@pytest.mark.asyncio
async def test_compress_runs_all_chunks(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="compressed")
    chunks = [_reranked("a"), _reranked("b"), _reranked("c")]

    result = await compressor.compress("query", chunks)

    assert len(result) == 3
    assert mock_llm_provider.complete.call_count == 3


@pytest.mark.asyncio
async def test_compress_empty_list_returns_empty(compressor):
    result = await compressor.compress("query", [])

    assert result == []
