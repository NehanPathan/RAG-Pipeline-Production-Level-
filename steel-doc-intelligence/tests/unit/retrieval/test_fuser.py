import uuid

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.retrieval.fusers.duplicate_remover import DuplicateRemover
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.fusers.rrf_fuser import RRFFusion
from src.retrieval.fusers.score_normalizer import ScoreNormalizer


@pytest.fixture
def fuser():
    return Fuser(rrf=RRFFusion(k=60), normalizer=ScoreNormalizer(), deduplicator=DuplicateRemover())


@pytest.mark.asyncio
async def test_fuse_runs_full_pipeline(fuser):
    chunk = DocumentChunk(document_id=uuid.uuid4(), content="hello", position=0)

    result = await fuser.fuse(
        vector_results=[ScoredChunk(chunk=chunk, score=0.9, rank=1)],
        bm25_results=[BM25ScoredChunk(chunk=chunk, bm25_score=10.0, rank=1)],
    )

    assert len(result) == 1
    assert result[0].normalized_vector_score == 1.0
    assert result[0].normalized_bm25_score == 1.0
    assert result[0].rank == 1


@pytest.mark.asyncio
async def test_fuse_dedups_exact_duplicate_content_across_chunk_ids(fuser):
    chunk_a = DocumentChunk(document_id=uuid.uuid4(), content="dup", position=0)
    chunk_b = DocumentChunk(document_id=uuid.uuid4(), content="dup", position=0)

    result = await fuser.fuse(
        vector_results=[ScoredChunk(chunk=chunk_a, score=0.9, rank=1)],
        bm25_results=[BM25ScoredChunk(chunk=chunk_b, bm25_score=10.0, rank=1)],
    )

    assert len(result) == 1
