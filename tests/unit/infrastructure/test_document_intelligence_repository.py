import uuid
from unittest.mock import AsyncMock, MagicMock

from src.domain.value_objects.document_intelligence import (
    DocumentIntelligenceSummary,
    LayoutSummary,
    SimilarityEdge,
)
from src.infrastructure.database.postgres.document_intelligence_repository import (
    PostgresDocumentIntelligenceRepository,
)
from src.infrastructure.database.postgres.models import DocumentIntelligenceModel


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _repo_with_fake_session(execute_result=None):
    session = AsyncMock()
    if execute_result is not None:
        session.execute = AsyncMock(return_value=execute_result)
    session_factory = MagicMock(return_value=_FakeSessionContext(session))
    return PostgresDocumentIntelligenceRepository(session_factory), session


def _summary(document_id: uuid.UUID) -> DocumentIntelligenceSummary:
    return DocumentIntelligenceSummary(
        document_id=document_id,
        ocr_engine="tesseract",
        ocr_ran=True,
        ocr_confidence_avg=0.9,
        ocr_processing_time_ms=150.0,
        ocr_language="en",
        embedding_model_chunking="BAAI/bge-m3",
        embedding_model_retrieval="text-embedding-3-large",
        layout=LayoutSummary(headings=[{"text": "Intro", "level": 0, "page_number": 1}], tables_count=2),
        semantic_graph=[SimilarityEdge(chunk_id_a=uuid.uuid4(), chunk_id_b=uuid.uuid4(), similarity=0.87)],
    )


async def test_save_executes_upsert_and_commits():
    repo, session = _repo_with_fake_session()
    document_id = uuid.uuid4()

    await repo.save(_summary(document_id))

    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_get_by_document_returns_none_when_missing():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    repo, _session = _repo_with_fake_session(execute_result=result)

    summary = await repo.get_by_document(uuid.uuid4())

    assert summary is None


async def test_get_by_document_maps_model_back_to_domain_summary():
    document_id = uuid.uuid4()
    chunk_id_a, chunk_id_b = uuid.uuid4(), uuid.uuid4()
    model = DocumentIntelligenceModel(
        id=uuid.uuid4(),
        document_id=document_id,
        ocr_engine="tesseract",
        ocr_ran=True,
        ocr_confidence_avg=0.75,
        ocr_processing_time_ms=200.0,
        ocr_language="en",
        embedding_model_chunking="BAAI/bge-m3",
        embedding_model_retrieval="text-embedding-3-large",
        layout_outline={
            "headings": [{"text": "Intro", "level": 0, "page_number": 1}],
            "outline": [],
            "tables_count": 1,
            "figures_count": 0,
            "lists_count": 0,
            "forms_count": 0,
            "footnotes_count": 0,
        },
        semantic_graph=[
            {"chunk_id_a": str(chunk_id_a), "chunk_id_b": str(chunk_id_b), "similarity": 0.5}
        ],
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = model
    repo, _session = _repo_with_fake_session(execute_result=result)

    summary = await repo.get_by_document(document_id)

    assert summary.document_id == document_id
    assert summary.ocr_confidence_avg == 0.75
    assert summary.layout.headings == [{"text": "Intro", "level": 0, "page_number": 1}]
    assert summary.layout.tables_count == 1
    assert summary.semantic_graph[0].chunk_id_a == chunk_id_a
    assert summary.semantic_graph[0].similarity == 0.5
