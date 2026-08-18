import uuid

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk
from src.retrieval.fusers.score_normalizer import ScoreNormalizer


def _fused(vector_score=None, bm25_score=None):
    return FusedChunk(
        chunk=DocumentChunk(document_id=uuid.uuid4(), content="c", position=0),
        rrf_score=0.0,
        vector_score=vector_score,
        bm25_score=bm25_score,
    )


def test_normalize_min_max_scales_to_zero_one():
    fused = [_fused(vector_score=0.2), _fused(vector_score=0.6), _fused(vector_score=1.0)]
    normalizer = ScoreNormalizer()

    result = normalizer.normalize(fused)

    assert result[0].normalized_vector_score == pytest.approx(0.0)
    assert result[1].normalized_vector_score == pytest.approx(0.5)
    assert result[2].normalized_vector_score == pytest.approx(1.0)


def test_normalize_handles_constant_scores_as_one():
    fused = [_fused(vector_score=0.7), _fused(vector_score=0.7)]
    normalizer = ScoreNormalizer()

    result = normalizer.normalize(fused)

    assert all(c.normalized_vector_score == 1.0 for c in result)


def test_normalize_skips_none_scores():
    fused = [_fused(vector_score=0.5), _fused(vector_score=None)]
    normalizer = ScoreNormalizer()

    result = normalizer.normalize(fused)

    assert result[0].normalized_vector_score is not None
    assert result[1].normalized_vector_score is None


def test_normalize_handles_vector_and_bm25_independently():
    fused = [_fused(vector_score=0.2, bm25_score=5.0), _fused(vector_score=0.8, bm25_score=15.0)]
    normalizer = ScoreNormalizer()

    result = normalizer.normalize(fused)

    assert result[0].normalized_vector_score == pytest.approx(0.0)
    assert result[1].normalized_vector_score == pytest.approx(1.0)
    assert result[0].normalized_bm25_score == pytest.approx(0.0)
    assert result[1].normalized_bm25_score == pytest.approx(1.0)


def test_normalize_empty_list_does_not_raise():
    assert ScoreNormalizer().normalize([]) == []
