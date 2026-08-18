from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.governance.retention import RetentionResult, RetentionService


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _service():
    service = RetentionService(
        document_repo=AsyncMock(),
        chunk_repo=AsyncMock(),
        vector_repo=AsyncMock(),
        search_repo=AsyncMock(),
        query_pipeline=AsyncMock(),
    )
    return service


def _session_yielding(document_ids):
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(document_id,) for document_id in document_ids]
    session.execute = AsyncMock(return_value=result)
    return session


def _patched_session(document_ids):
    session = _session_yielding(document_ids)
    return patch(
        "src.governance.retention.get_session_factory",
        return_value=MagicMock(return_value=_FakeSessionContext(session)),
    ), session


@pytest.fixture(autouse=True)
def _no_real_audit():
    with patch("src.governance.retention.audit_record", new=AsyncMock()) as record:
        yield record


class TestRetentionResult:
    def test_as_dict_carries_every_field(self):
        result = RetentionResult(
            scanned=3, purged=2, failed=1, dry_run=False, document_ids=["a", "b", "c"]
        )

        assert result.as_dict() == {
            "scanned": 3,
            "purged": 2,
            "failed": 1,
            "dry_run": False,
            "document_ids": ["a", "b", "c"],
        }


class TestFindExpired:
    async def test_returns_the_ids_the_query_matched(self):
        expected = [uuid.uuid4(), uuid.uuid4()]
        session_patch, _session = _patched_session(expected)

        with session_patch:
            found = await _service().find_expired()

        assert found == expected

    async def test_accepts_an_explicit_cutoff(self):
        """Passing `now` is what makes the deadline arithmetic testable without
        waiting for wall-clock time to pass."""
        session_patch, session = _patched_session([])

        with session_patch:
            found = await _service().find_expired(now=datetime(2030, 1, 1, tzinfo=UTC))

        assert found == []
        session.execute.assert_awaited_once()


class TestPurgeExpired:
    async def test_defaults_to_a_dry_run_that_deletes_nothing(self):
        """Irreversible bulk deletion driven by a date column should require
        someone to opt in after reading what it intends to remove."""
        document_id = uuid.uuid4()
        service = _service()
        session_patch, _session = _patched_session([document_id])

        with session_patch:
            result = await service.purge_expired()

        assert result.dry_run is True
        assert result.scanned == 1
        assert result.purged == 0
        assert result.document_ids == [str(document_id)]
        service._documents.delete.assert_not_called()
        service._vectors.delete_by_document.assert_not_called()

    async def test_purges_every_store_a_document_is_reachable_from(self):
        """A document surviving in any one store is still answerable, so all
        four have to go: Qdrant, Elasticsearch, the semantic cache, Postgres."""
        document_id = uuid.uuid4()
        service = _service()
        session_patch, _session = _patched_session([document_id])

        with session_patch:
            result = await service.purge_expired(dry_run=False)

        assert result.purged == 1
        assert result.failed == 0
        service._vectors.delete_by_document.assert_awaited_once_with(document_id)
        service._search.delete_by_document.assert_awaited_once_with(document_id)
        service._pipeline.invalidate_cached_document.assert_awaited_once_with(document_id)
        service._documents.delete.assert_awaited_once_with(document_id)

    async def test_records_an_audit_entry_per_purged_document(self, _no_real_audit):
        session_patch, _session = _patched_session([uuid.uuid4()])

        with session_patch:
            await _service().purge_expired(dry_run=False)

        assert _no_real_audit.await_args.kwargs["control_id"] == "C-MAN-03"
        assert _no_real_audit.await_args.kwargs["resource_type"] == "document"

    async def test_one_failure_does_not_abandon_the_remaining_documents(self):
        first, second = uuid.uuid4(), uuid.uuid4()
        service = _service()
        service._vectors.delete_by_document = AsyncMock(
            side_effect=[RuntimeError("qdrant down"), None]
        )
        session_patch, _session = _patched_session([first, second])

        with session_patch:
            result = await service.purge_expired(dry_run=False)

        assert result.scanned == 2
        assert result.purged == 1
        assert result.failed == 1
        # The document whose vector delete failed must not be removed from
        # Postgres, or it becomes unreachable-but-not-deleted.
        service._documents.delete.assert_awaited_once_with(second)

    async def test_nothing_expired_is_a_clean_no_op(self):
        session_patch, _session = _patched_session([])

        with session_patch:
            result = await _service().purge_expired(dry_run=False)

        assert (result.scanned, result.purged, result.failed) == (0, 0, 0)
