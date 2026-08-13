from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.document import Document, DocumentStatus
from src.ingestion.pipeline import IngestionPipeline


@pytest.fixture
def pipeline(
    mock_llm_enricher,
    chunker,
    mock_embedding_provider,
    mock_document_repo,
    mock_chunk_repo,
    mock_vector_repo,
    mock_search_repo,
    sample_user_id,
):
    from src.ingestion.chunkers.chunking_strategy import ParentChildOnlyStrategy
    from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy
    from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
    from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
    from src.ingestion.loaders.base import RawDocument, TextBlock
    from src.ingestion.ocr.detector import OCRDetector
    from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService

    mock_loader = AsyncMock()
    mock_loader.supports = MagicMock(return_value=True)
    mock_loader.name = "mock"
    mock_loader.load = AsyncMock(
        return_value=RawDocument(
            file_path=Path("/tmp/test.pdf"),
            file_name="test.pdf",
            mime_type="application/pdf",
            text_blocks=[TextBlock(text="Sample text for testing." * 20, page_number=1)],
            tables=[],
            page_count=1,
            word_count=100,
            loader_name="mock",
        )
    )

    # min_words_per_page=10 with word_count=100/page_count=1 above => the
    # OCRDetector classifies this fixture's document as searchable, so
    # ocr_provider (an unconfigured AsyncMock) is never actually invoked.
    parsing_service = DocumentParsingService(
        ocr_detector=OCRDetector(),
        ocr_provider=AsyncMock(),
        labeled_analyzer=LabeledLayoutAnalyzer(),
        heuristic_analyzer=HeuristicLayoutAnalyzer(),
    )

    return IngestionPipeline(
        loaders=[mock_loader],
        enricher=mock_llm_enricher,
        parsing_service=parsing_service,
        chunking_strategy=ParentChildOnlyStrategy(chunker),
        embedding_strategy=EmbeddingStrategy(
            chunking_provider=mock_embedding_provider,
            retrieval_provider=mock_embedding_provider,
        ),
        document_repo=mock_document_repo,
        chunk_repo=mock_chunk_repo,
        vector_repo=mock_vector_repo,
        search_repo=mock_search_repo,
    )


@pytest.fixture
def document(sample_user_id) -> Document:
    return Document(
        file_name="test.pdf",
        file_type="pdf",
        file_size_bytes=1024,
        user_id=sample_user_id,
    )


@pytest.mark.asyncio
async def test_successful_ingestion(pipeline, document, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf content")

    result = await pipeline.ingest(document, file_path)

    assert result.status == DocumentStatus.INDEXED
    assert result.chunks_created > 0
    assert result.error is None


@pytest.mark.asyncio
async def test_document_marked_indexed_on_success(pipeline, document, mock_document_repo, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    await pipeline.ingest(document, file_path)

    # update should be called at least twice (mark_processing + mark_indexed)
    assert mock_document_repo.update.call_count >= 2


@pytest.mark.asyncio
async def test_document_marked_failed_on_error(pipeline, document, mock_chunk_repo, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    mock_chunk_repo.save_batch = AsyncMock(side_effect=RuntimeError("DB down"))

    result = await pipeline.ingest(document, file_path)

    assert result.status == DocumentStatus.FAILED
    assert result.error is not None


@pytest.mark.asyncio
async def test_vector_repo_called_with_chunks(pipeline, document, mock_vector_repo, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    await pipeline.ingest(document, file_path)

    mock_vector_repo.upsert_batch.assert_called_once()


@pytest.mark.asyncio
async def test_search_repo_called_with_chunks(pipeline, document, mock_search_repo, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    await pipeline.ingest(document, file_path)

    mock_search_repo.index_batch.assert_called_once()


@pytest.mark.asyncio
async def test_intelligence_recorder_called_with_models_when_provided(
    mock_llm_enricher,
    chunker,
    mock_embedding_provider,
    mock_document_repo,
    mock_chunk_repo,
    mock_vector_repo,
    mock_search_repo,
    document,
    tmp_path,
):
    from src.ingestion.chunkers.chunking_strategy import ParentChildOnlyStrategy
    from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy
    from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
    from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
    from src.ingestion.loaders.base import RawDocument, TextBlock
    from src.ingestion.ocr.detector import OCRDetector
    from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService

    mock_loader = AsyncMock()
    mock_loader.supports = MagicMock(return_value=True)
    mock_loader.name = "mock"
    mock_loader.load = AsyncMock(
        return_value=RawDocument(
            file_path=Path("/tmp/test.pdf"),
            file_name="test.pdf",
            mime_type="application/pdf",
            text_blocks=[TextBlock(text="Sample text for testing." * 20, page_number=1)],
            tables=[],
            page_count=1,
            word_count=100,
            loader_name="mock",
        )
    )
    intelligence_recorder = AsyncMock()

    pipeline = IngestionPipeline(
        loaders=[mock_loader],
        enricher=mock_llm_enricher,
        parsing_service=DocumentParsingService(
            ocr_detector=OCRDetector(),
            ocr_provider=AsyncMock(),
            labeled_analyzer=LabeledLayoutAnalyzer(),
            heuristic_analyzer=HeuristicLayoutAnalyzer(),
        ),
        chunking_strategy=ParentChildOnlyStrategy(chunker),
        embedding_strategy=EmbeddingStrategy(
            chunking_provider=mock_embedding_provider,
            retrieval_provider=mock_embedding_provider,
        ),
        document_repo=mock_document_repo,
        chunk_repo=mock_chunk_repo,
        vector_repo=mock_vector_repo,
        search_repo=mock_search_repo,
        intelligence_recorder=intelligence_recorder,
    )

    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    await pipeline.ingest(document, file_path)

    intelligence_recorder.save.assert_awaited_once()
    _, kwargs = intelligence_recorder.save.call_args
    assert kwargs["chunking_model_id"] == mock_embedding_provider.model_id
    assert kwargs["retrieval_model_id"] == mock_embedding_provider.model_id


@pytest.mark.asyncio
async def test_ingestion_succeeds_without_intelligence_recorder(pipeline, document, tmp_path):
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"fake pdf")

    result = await pipeline.ingest(document, file_path)

    assert result.status == DocumentStatus.INDEXED


# --- Revision registration -------------------------------------------------
#
# RegisterRevision was fully implemented but never called, so every chunk in
# the corpus carried a null drawing_number and revision_label -- the two
# fields a steel drawing register is organised by. Search could filter on
# them and would always match nothing.


def _pipeline_with_register(base_pipeline, register, identity):
    """The same pipeline, with a drawing register and an enricher that
    produces the given identity."""
    from src.domain.entities.document import DocumentMetadata
    from src.ingestion.extractors.drawing_identity import CUSTOM_METADATA_KEY

    metadata = DocumentMetadata(domain="Engineering", tags=[])
    if identity is not None:
        metadata.custom_metadata[CUSTOM_METADATA_KEY] = identity
    base_pipeline._enricher.enrich = AsyncMock(return_value=metadata)
    base_pipeline._register_revision = register
    return base_pipeline


def _register(drawing_number="S-104", is_current=True):
    from src.application.use_cases.register_revision import RevisionRegistration
    from src.domain.entities.project import Drawing

    register = AsyncMock()

    async def execute(document, drawing_number=drawing_number, revision_label="C", **kwargs):
        drawing = Drawing(drawing_number=drawing_number)
        document.drawing_id = drawing.id
        document.revision_label = revision_label
        document.is_latest = is_current
        return RevisionRegistration(
            drawing=drawing,
            document=document,
            superseded_document_id=None,
            is_current=is_current,
        )

    register.execute = AsyncMock(side_effect=execute)
    return register


async def test_chunks_carry_the_drawing_number_and_revision(pipeline, document, mock_chunk_repo):
    """The point of wiring it in: both fields reach every chunk, and so both
    search backends, which is where they are filtered on."""
    register = _register()
    _pipeline_with_register(
        pipeline,
        register,
        {"drawing_number": "S-104", "revision_label": "C", "reason": "found 3x"},
    )

    await pipeline.ingest(document, Path("/tmp/test.pdf"))

    register.execute.assert_awaited_once()
    saved = mock_chunk_repo.save_batch.await_args.args[0]
    assert saved
    assert all(c.drawing_number == "S-104" for c in saved)
    assert all(c.revision_label == "C" for c in saved)
    assert all(c.is_latest for c in saved)


async def test_a_superseded_revision_marks_its_own_chunks_not_current(
    pipeline, document, mock_chunk_repo
):
    """Uploading Rev B after Rev C must not claim to be current."""
    _pipeline_with_register(
        pipeline,
        _register(is_current=False),
        {"drawing_number": "S-104", "revision_label": "B", "reason": "found 2x"},
    )

    await pipeline.ingest(document, Path("/tmp/test.pdf"))

    saved = mock_chunk_repo.save_batch.await_args.args[0]
    assert saved
    assert not any(c.is_latest for c in saved)


async def test_a_document_with_no_drawing_identity_is_still_ingested(
    pipeline, document, mock_chunk_repo
):
    """A specification is not a drawing. It has no revision family, and that
    is not a failure."""
    register = _register()
    _pipeline_with_register(pipeline, register, None)

    result = await pipeline.ingest(document, Path("/tmp/test.pdf"))

    register.execute.assert_not_awaited()
    assert result.status is DocumentStatus.INDEXED
    assert result.chunks_created > 0
    assert all(c.drawing_number is None for c in mock_chunk_repo.save_batch.await_args.args[0])


async def test_a_drawing_number_without_a_revision_is_not_registered(
    pipeline, document, mock_chunk_repo
):
    """With nothing to sort on, every upload of the sheet would claim to
    supersede the last, in upload order -- which is what a register exists
    to prevent."""
    register = _register()
    _pipeline_with_register(
        pipeline, register, {"drawing_number": "S-104", "revision_label": None, "reason": "x"}
    )

    result = await pipeline.ingest(document, Path("/tmp/test.pdf"))

    register.execute.assert_not_awaited()
    assert result.status is DocumentStatus.INDEXED


async def test_a_failing_registration_does_not_fail_the_upload(pipeline, document):
    """A register with a gap in it is better than a rejected document: the
    content is still worth searching, and the number can be set by hand."""
    register = AsyncMock()
    register.execute = AsyncMock(side_effect=RuntimeError("drawings table unavailable"))
    _pipeline_with_register(
        pipeline, register, {"drawing_number": "S-104", "revision_label": "C", "reason": "x"}
    )

    result = await pipeline.ingest(document, Path("/tmp/test.pdf"))

    assert result.status is DocumentStatus.INDEXED
    assert result.chunks_created > 0


async def test_no_register_configured_leaves_the_fields_null(pipeline, document, mock_chunk_repo):
    """A plain document corpus has no revisions to track."""
    _pipeline_with_register(
        pipeline, None, {"drawing_number": "S-104", "revision_label": "C", "reason": "x"}
    )

    result = await pipeline.ingest(document, Path("/tmp/test.pdf"))

    assert result.status is DocumentStatus.INDEXED
    assert all(c.drawing_number is None for c in mock_chunk_repo.save_batch.await_args.args[0])
