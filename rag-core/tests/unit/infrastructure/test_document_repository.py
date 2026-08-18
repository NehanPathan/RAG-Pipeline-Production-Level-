from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.document import Document, DocumentMetadata, DocumentStatus
from src.domain.value_objects.sensitivity import Sensitivity
from src.infrastructure.database.postgres.document_repository import (
    PostgresDocumentRepository,
    _to_entity,
)
from src.infrastructure.database.postgres.models import DocumentMetadataModel, DocumentModel


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _repo():
    session = AsyncMock()
    session.add = MagicMock()
    return PostgresDocumentRepository(MagicMock(return_value=_FakeSessionContext(session))), session


def _document(**overrides) -> Document:
    fields = {
        "file_name": "policy.pdf",
        "file_type": "pdf",
        "file_size_bytes": 2048,
        "user_id": uuid.uuid4(),
    }
    fields.update(overrides)
    return Document(**fields)


def _model(**overrides):
    """A row-shaped stand-in for DocumentModel.

    SimpleNamespace rather than the ORM class: `_to_entity` only reads
    attributes, and an unflushed ORM instance leaves every unset column as
    None, which hides which values a test actually cares about.
    """
    fields = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "file_name": "policy.pdf",
        "file_type": "pdf",
        "file_size_bytes": 2048,
        "file_path": "/data/policy.pdf",
        "status": "indexed",
        "error_message": None,
        "page_count": 4,
        "word_count": 900,
        "loader_used": "docling",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "indexed_at": None,
        "sensitivity": "confidential",
        "retention_until": None,
        "metadata_record": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _paged_session(session, rows, total):
    """Wire the two executes `list_by_user` performs: a count, then the page."""
    count_result = MagicMock()
    count_result.scalar_one.return_value = total
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(side_effect=[count_result, page_result])
    return session


def _bound_values(session, call_index):
    statement = session.execute.await_args_list[call_index].args[0]
    return list(statement.compile().params.values())


def _sql(session, call_index):
    return str(session.execute.await_args_list[call_index].args[0])


class TestToEntity:
    def test_maps_the_row_onto_the_entity(self):
        model = _model()

        document = _to_entity(model)

        assert document.id == model.id
        assert document.file_name == "policy.pdf"
        assert document.status is DocumentStatus.INDEXED
        assert document.sensitivity is Sensitivity.CONFIDENTIAL

    def test_a_null_classification_reads_as_internal_not_public(self):
        """A NULL column predates classification; INTERNAL is the fail-closed
        reading."""
        assert _to_entity(_model(sensitivity=None)).sensitivity is Sensitivity.INTERNAL

    def test_a_missing_metadata_row_leaves_the_defaults(self):
        document = _to_entity(_model(metadata_record=None))

        assert document.metadata == DocumentMetadata()

    def test_metadata_nulls_become_defaults_rather_than_none(self):
        record = SimpleNamespace(
            summary=None, tags=None, domain=None, language=None, entities=None,
            custom_metadata=None,
        )

        metadata = _to_entity(_model(metadata_record=record)).metadata

        assert metadata.summary == ""
        assert metadata.tags == []
        assert metadata.domain == "general"
        assert metadata.language == "en"

    def test_metadata_values_are_carried_across(self):
        record = SimpleNamespace(
            summary="A refund policy.",
            tags=["refund"],
            domain="Operations",
            language="fr",
            entities=[{"type": "ORG", "text": "ACME"}],
            custom_metadata={"source": "intranet"},
        )

        metadata = _to_entity(_model(metadata_record=record)).metadata

        assert metadata.summary == "A refund policy."
        assert metadata.domain == "Operations"
        assert metadata.entities == [{"type": "ORG", "text": "ACME"}]

    def test_a_blank_file_path_stays_a_string(self):
        assert _to_entity(_model(file_path=None)).file_path == ""


class TestSave:
    async def test_persists_the_classification_alongside_the_document(self):
        repo, session = _repo()
        document = _document(sensitivity=Sensitivity.RESTRICTED)

        returned = await repo.save(document)

        model = session.add.call_args[0][0]
        assert isinstance(model, DocumentModel)
        assert model.id == document.id
        assert model.sensitivity == "restricted"
        assert model.status == "pending"
        session.commit.assert_awaited_once()
        assert returned is document

    async def test_an_empty_file_path_is_stored_as_null(self):
        repo, session = _repo()

        await repo.save(_document(file_path=""))

        assert session.add.call_args[0][0].file_path is None


class TestGetById:
    async def test_returns_the_entity_when_the_row_exists(self):
        repo, session = _repo()
        model = _model()
        result = MagicMock()
        result.scalar_one_or_none.return_value = model
        session.execute = AsyncMock(return_value=result)

        document = await repo.get_by_id(model.id)

        assert document is not None
        assert document.id == model.id

    async def test_returns_none_for_an_unknown_id(self):
        repo, session = _repo()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        assert await repo.get_by_id(uuid.uuid4()) is None


class TestListByUser:
    async def test_returns_the_page_and_the_total(self):
        repo, session = _repo()
        _paged_session(session, [_model(), _model()], total=7)

        documents, total = await repo.list_by_user(uuid.uuid4())

        assert len(documents) == 2
        assert total == 7

    async def test_search_wildcards_in_user_input_are_escaped(self):
        """Without this a search for "%" matches every document, and "_"
        silently means "any character"."""
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), search="50% off_deal")

        assert "%50\\% off\\_deal%" in _bound_values(session, 0)

    async def test_a_backslash_in_the_search_term_is_escaped_first(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), search="a\\b")

        assert "%a\\\\b%" in _bound_values(session, 0)

    async def test_the_clearance_filter_applies_before_the_count(self):
        """Otherwise the total counts documents the caller may not read and
        the last page comes back short."""
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), sensitivity_in=["public"])

        assert "sensitivity IN" in _sql(session, 0)

    async def test_internal_clearance_also_admits_unclassified_rows(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), sensitivity_in=["public", "internal"])

        assert "sensitivity IS NULL" in _sql(session, 0)

    async def test_a_narrower_clearance_does_not_admit_unclassified_rows(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), sensitivity_in=["public"])

        assert "sensitivity IS NULL" not in _sql(session, 0)

    async def test_status_and_file_type_narrow_the_query(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(
            uuid.uuid4(), status=DocumentStatus.FAILED, file_type="pdf"
        )

        values = _bound_values(session, 0)
        assert "failed" in values
        assert "pdf" in values

    async def test_filtering_by_domain_joins_the_metadata_table(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), domain="Operations")

        assert "document_metadata" in _sql(session, 0)

    async def test_paging_offsets_by_whole_pages(self):
        repo, session = _repo()
        _paged_session(session, [], total=0)

        await repo.list_by_user(uuid.uuid4(), page=3, size=20)

        assert 40 in _bound_values(session, 1)


class TestUpdate:
    def _existing(self):
        return SimpleNamespace(
            status=None, error_message=None, page_count=None, word_count=None,
            loader_used=None, indexed_at=None, sensitivity=None, retention_until=None,
        )

    async def test_copies_the_mutable_fields_onto_the_row(self):
        repo, session = _repo()
        model = self._existing()
        session.get = AsyncMock(return_value=model)
        metadata_result = MagicMock()
        metadata_result.scalar_one_or_none.return_value = SimpleNamespace()
        session.execute = AsyncMock(return_value=metadata_result)
        document = _document(status=DocumentStatus.INDEXED, page_count=9)

        await repo.update(document)

        assert model.status == "indexed"
        assert model.page_count == 9
        session.commit.assert_awaited_once()

    async def test_creates_the_metadata_row_when_there_is_none_yet(self):
        repo, session = _repo()
        session.get = AsyncMock(return_value=self._existing())
        metadata_result = MagicMock()
        metadata_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=metadata_result)
        document = _document()
        document.metadata = DocumentMetadata(summary="A summary.", tags=["a"])

        await repo.update(document)

        added = session.add.call_args[0][0]
        assert isinstance(added, DocumentMetadataModel)
        assert added.summary == "A summary."

    async def test_updating_a_missing_document_is_an_error(self):
        repo, session = _repo()
        session.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await repo.update(_document())


class TestDelete:
    async def test_deletes_and_reports_success(self):
        repo, session = _repo()
        model = SimpleNamespace()
        session.get = AsyncMock(return_value=model)

        assert await repo.delete(uuid.uuid4()) is True
        session.delete.assert_awaited_once_with(model)
        session.commit.assert_awaited_once()

    async def test_deleting_a_missing_document_reports_false(self):
        repo, session = _repo()
        session.get = AsyncMock(return_value=None)

        assert await repo.delete(uuid.uuid4()) is False
        session.commit.assert_not_awaited()
