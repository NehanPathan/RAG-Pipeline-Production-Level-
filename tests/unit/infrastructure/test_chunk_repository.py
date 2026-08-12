import uuid
from unittest.mock import AsyncMock, MagicMock

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.infrastructure.database.postgres.chunk_repository import PostgresChunkRepository, _to_entity
from src.infrastructure.database.postgres.models import DocumentChunkModel


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _repo_with_fake_session():
    session = AsyncMock()
    session.add = MagicMock()
    session_factory = MagicMock(return_value=_FakeSessionContext(session))
    return PostgresChunkRepository(session_factory), session


async def test_save_batch_persists_additive_metadata_fields():
    repo, session = _repo_with_fake_session()
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        content="Some chunk content.",
        position=0,
        chunk_type=ChunkType.CHILD,
        chunk_metadata=ChunkMetadata(
            page_number=2,
            section_title="Chapter 1",
            heading_level=1,
            semantic_cluster=3,
            ocr_confidence=0.87,
            language="en",
        ),
    )

    await repo.save_batch([chunk])

    saved_model = session.add.call_args[0][0]
    assert isinstance(saved_model, DocumentChunkModel)
    assert saved_model.section_title == "Chapter 1"
    assert saved_model.heading_level == 1
    assert saved_model.semantic_cluster == 3
    assert saved_model.ocr_confidence == 0.87
    assert saved_model.language == "en"


def test_to_entity_reconstructs_additive_metadata_fields():
    model = DocumentChunkModel(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_type="child",
        content="Some content",
        content_hash="hash",
        position=0,
        section_title="Chapter 2",
        heading_level=2,
        semantic_cluster=5,
        ocr_confidence=0.42,
        language="fr",
    )

    entity = _to_entity(model)

    assert entity.chunk_metadata.section_title == "Chapter 2"
    assert entity.chunk_metadata.heading_level == 2
    assert entity.chunk_metadata.semantic_cluster == 5
    assert entity.chunk_metadata.ocr_confidence == 0.42
    assert entity.chunk_metadata.language == "fr"
