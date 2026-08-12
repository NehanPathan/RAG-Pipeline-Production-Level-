import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.context_processor import ContextProcessor


def _reranked(content):
    chunk = DocumentChunk(document_id=uuid.uuid4(), content=content, position=0)
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    return RerankedChunk(fused=fused, rerank_score=0.9)


@pytest.fixture
def deduplicator():
    dedup = MagicMock()
    dedup.deduplicate = MagicMock(side_effect=lambda chunks: chunks)
    return dedup


@pytest.fixture
def compressor():
    comp = AsyncMock()
    comp.compress = AsyncMock(
        side_effect=lambda query, chunks: [
            CompressedChunk(reranked=c, compressed_content=c.chunk.content) for c in chunks
        ]
    )
    return comp


@pytest.fixture
def budget_manager():
    manager = MagicMock()
    manager.select = MagicMock(side_effect=lambda chunks: chunks)
    return manager


@pytest.fixture
def citation_preserver():
    preserver = MagicMock()
    preserver.build = MagicMock(return_value={})
    return preserver


@pytest.fixture
def processor(deduplicator, compressor, budget_manager, citation_preserver):
    return ContextProcessor(
        deduplicator=deduplicator,
        compressor=compressor,
        budget_manager=budget_manager,
        citation_preserver=citation_preserver,
    )


@pytest.mark.asyncio
async def test_process_runs_full_pipeline_in_order(
    processor, deduplicator, compressor, budget_manager, citation_preserver
):
    chunks = [_reranked("a"), _reranked("b")]

    selected, citations = await processor.process("query", chunks)

    deduplicator.deduplicate.assert_called_once_with(chunks)
    compressor.compress.assert_called_once()
    budget_manager.select.assert_called_once()
    citation_preserver.build.assert_called_once()
    assert len(selected) == 2
    assert citations == {}
