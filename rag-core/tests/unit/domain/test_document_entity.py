import uuid
from datetime import datetime

import pytest

from src.domain.entities.document import Document, DocumentStatus


def test_document_initial_status_is_pending(sample_document):
    assert sample_document.status == DocumentStatus.PENDING


def test_mark_processing_changes_status(sample_document):
    sample_document.mark_processing()
    assert sample_document.status == DocumentStatus.PROCESSING


def test_mark_indexed_sets_indexed_at(sample_document):
    before = datetime.utcnow()
    sample_document.mark_indexed()
    assert sample_document.status == DocumentStatus.INDEXED
    assert sample_document.indexed_at is not None
    assert sample_document.indexed_at >= before


def test_mark_failed_sets_error_message(sample_document):
    sample_document.mark_failed("Connection refused")
    assert sample_document.status == DocumentStatus.FAILED
    assert sample_document.error_message == "Connection refused"


def test_document_id_is_uuid(sample_document):
    assert isinstance(sample_document.id, uuid.UUID)


def test_content_hash_deterministic():
    from src.domain.entities.document import DocumentChunk
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="Hello world",
        position=0,
    )
    assert chunk.content_hash == chunk.content_hash
    assert len(chunk.content_hash) == 64


def test_chunk_has_embedding_false_when_empty():
    from src.domain.entities.document import DocumentChunk
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="test",
        position=0,
        embedding=[],
    )
    assert chunk.has_embedding() is False


def test_chunk_has_embedding_true_when_set():
    from src.domain.entities.document import DocumentChunk
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="test",
        position=0,
        embedding=[0.1, 0.2, 0.3],
    )
    assert chunk.has_embedding() is True
