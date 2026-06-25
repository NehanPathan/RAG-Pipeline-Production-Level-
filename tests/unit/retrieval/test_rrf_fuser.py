import uuid

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.retrieval.fusers.rrf_fuser import RRFFusion


def _chunk(chunk_id=None):
    return DocumentChunk(id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content="c", position=0)


def test_fuse_combines_scores_for_chunk_in_both_lists():
    chunk = _chunk()
    fusion = RRFFusion(k=60)

    fused = fusion.fuse(
        vector_results=[ScoredChunk(chunk=chunk, score=0.9, rank=1)],
        bm25_results=[BM25ScoredChunk(chunk=chunk, bm25_score=10.0, rank=2)],
    )

    assert len(fused) == 1
    expected = 1 / 61 + 1 / 62
    assert fused[0].rrf_score == pytest.approx(expected)
    assert fused[0].vector_score == 0.9
    assert fused[0].bm25_score == 10.0


def test_fuse_includes_chunk_only_in_vector_list():
    chunk = _chunk()
    fusion = RRFFusion(k=60)

    fused = fusion.fuse(vector_results=[ScoredChunk(chunk=chunk, score=0.9, rank=1)], bm25_results=[])

    assert len(fused) == 1
    assert fused[0].vector_score == 0.9
    assert fused[0].bm25_score is None
    assert fused[0].rrf_score == pytest.approx(1 / 61)


def test_fuse_includes_chunk_only_in_bm25_list():
    chunk = _chunk()
    fusion = RRFFusion(k=60)

    fused = fusion.fuse(
        vector_results=[], bm25_results=[BM25ScoredChunk(chunk=chunk, bm25_score=5.0, rank=3)]
    )

    assert len(fused) == 1
    assert fused[0].vector_score is None
    assert fused[0].bm25_score == 5.0


def test_fuse_ranks_by_combined_score_descending():
    chunk_a, chunk_b = _chunk(), _chunk()
    fusion = RRFFusion(k=60)

    fused = fusion.fuse(
        vector_results=[
            ScoredChunk(chunk=chunk_a, score=0.5, rank=1),
            ScoredChunk(chunk=chunk_b, score=0.4, rank=2),
        ],
        bm25_results=[BM25ScoredChunk(chunk=chunk_b, bm25_score=20.0, rank=1)],
    )

    # chunk_b appears in both lists (vector rank 2 + bm25 rank 1) vs chunk_a (vector rank 1 only)
    assert fused[0].chunk.id == chunk_b.id
    assert fused[0].rank == 1
    assert fused[1].chunk.id == chunk_a.id
    assert fused[1].rank == 2


def test_fuse_empty_lists_returns_empty():
    fusion = RRFFusion()

    assert fusion.fuse([], []) == []
