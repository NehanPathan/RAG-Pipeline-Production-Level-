from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.sensitivity_guard import SensitivityGuard


@dataclass
class _Result:
    """Stands in for ScoredChunk / BM25ScoredChunk / RerankedChunk, all of
    which the guard accepts via the `.chunk` attribute."""

    chunk: DocumentChunk


def _chunk(sensitivity: Sensitivity | None) -> _Result:
    chunk = DocumentChunk(document_id=uuid.uuid4(), content="body", position=0)
    if sensitivity is not None:
        chunk.sensitivity = sensitivity
    return _Result(chunk=chunk)


class TestSensitivityGuard:
    def test_permits_content_at_or_below_clearance(self):
        guard = SensitivityGuard()
        results = [_chunk(Sensitivity.PUBLIC), _chunk(Sensitivity.INTERNAL)]
        permitted, blocked = guard.filter(results, Sensitivity.INTERNAL)
        assert len(permitted) == 2
        assert blocked == 0

    def test_blocks_content_above_clearance(self):
        guard = SensitivityGuard()
        results = [_chunk(Sensitivity.PUBLIC), _chunk(Sensitivity.RESTRICTED)]
        permitted, blocked = guard.filter(results, Sensitivity.PUBLIC)
        assert len(permitted) == 1
        assert blocked == 1
        assert permitted[0].chunk.sensitivity is Sensitivity.PUBLIC

    def test_admin_clearance_sees_everything(self):
        guard = SensitivityGuard()
        results = [_chunk(s) for s in Sensitivity]
        permitted, blocked = guard.filter(results, Sensitivity.RESTRICTED)
        assert len(permitted) == len(list(Sensitivity))
        assert blocked == 0

    def test_public_clearance_sees_only_public(self):
        guard = SensitivityGuard()
        results = [_chunk(s) for s in Sensitivity]
        permitted, blocked = guard.filter(results, Sensitivity.PUBLIC)
        assert [r.chunk.sensitivity for r in permitted] == [Sensitivity.PUBLIC]
        assert blocked == 3

    def test_preserves_order_of_permitted_results(self):
        """Retrieval order is relevance order; the guard must not reshuffle it."""
        guard = SensitivityGuard()
        results = [_chunk(Sensitivity.PUBLIC) for _ in range(4)]
        ids = [r.chunk.id for r in results]
        permitted, _ = guard.filter(results, Sensitivity.PUBLIC)
        assert [r.chunk.id for r in permitted] == ids

    def test_empty_input_is_handled(self):
        permitted, blocked = SensitivityGuard().filter([], Sensitivity.PUBLIC)
        assert permitted == []
        assert blocked == 0

    @pytest.mark.parametrize(
        "default", [Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL]
    )
    def test_unlabelled_chunk_uses_configured_default(self, default):
        """A chunk whose sensitivity attribute is missing entirely (legacy
        payload) is judged against the default, never treated as public."""

        class _Bare:
            pass

        bare = _Bare()
        bare.chunk = type("C", (), {})()  # no `sensitivity` attribute at all

        guard = SensitivityGuard(default_sensitivity=default)
        permitted, blocked = guard.filter([bare], Sensitivity.PUBLIC)
        assert permitted == []
        assert blocked == 1
