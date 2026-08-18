import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.ingestion.layout.models import DocumentLayout, Heading, OutlineNode, TableRef
from src.ingestion.loaders.base import RawDocument
from src.ingestion.ocr.models import OCRMetadata
from src.ingestion.parsing.intelligence_recorder import DocumentIntelligenceRecorder
from src.ingestion.parsing.parsed_document import ParsedDocument


def _parsed_document(layout: DocumentLayout, ocr_metadata: OCRMetadata) -> ParsedDocument:
    raw = RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=[],
        page_count=1,
    )
    return ParsedDocument(raw=raw, ocr_metadata=ocr_metadata, layout=layout)


def _chunk(position: int, embedding: list[float] | None = None) -> DocumentChunk:
    return DocumentChunk(
        document_id=uuid.uuid4(),
        content=f"chunk {position}",
        position=position,
        embedding=embedding or [],
    )


async def test_save_builds_summary_with_ocr_and_embedding_fields():
    repository = AsyncMock()
    recorder = DocumentIntelligenceRecorder(repository)
    parsed = _parsed_document(
        DocumentLayout(),
        OCRMetadata(engine="tesseract", ran=True, confidence=0.92, processing_time_ms=120.0, language="en"),
    )

    await recorder.save(
        uuid.uuid4(), parsed, chunks=[], chunking_model_id="BAAI/bge-m3", retrieval_model_id="text-embedding-3-large"
    )

    repository.save.assert_awaited_once()
    summary = repository.save.call_args[0][0]
    assert summary.ocr_engine == "tesseract"
    assert summary.ocr_ran is True
    assert summary.ocr_confidence_avg == 0.92
    assert summary.embedding_model_chunking == "BAAI/bge-m3"
    assert summary.embedding_model_retrieval == "text-embedding-3-large"


async def test_save_summarizes_layout_counts_and_outline():
    repository = AsyncMock()
    recorder = DocumentIntelligenceRecorder(repository)
    layout = DocumentLayout(
        headings=[Heading(text="Intro", level=0, page_number=1)],
        tables=[TableRef(caption="Pricing", page_number=2)],
        outline=[OutlineNode(title="Intro", level=0, page_number=1)],
    )
    parsed = _parsed_document(layout, OCRMetadata.skipped("searchable"))

    await recorder.save(uuid.uuid4(), parsed, chunks=[], chunking_model_id="bge", retrieval_model_id="openai")

    summary = repository.save.call_args[0][0]
    assert summary.layout.headings == [{"text": "Intro", "level": 0, "page_number": 1}]
    assert summary.layout.tables_count == 1
    assert summary.layout.outline[0]["title"] == "Intro"
    assert summary.layout.outline[0]["children"] == []


async def test_semantic_graph_built_from_consecutive_embedded_chunks():
    repository = AsyncMock()
    recorder = DocumentIntelligenceRecorder(repository)
    parsed = _parsed_document(DocumentLayout(), OCRMetadata.skipped("skip"))

    chunks = [
        _chunk(0, embedding=[1.0, 0.0]),
        _chunk(1, embedding=[1.0, 0.0]),
        _chunk(2, embedding=[0.0, 1.0]),
    ]

    await recorder.save(uuid.uuid4(), parsed, chunks=chunks, chunking_model_id="bge", retrieval_model_id="openai")

    summary = repository.save.call_args[0][0]
    assert len(summary.semantic_graph) == 2
    assert summary.semantic_graph[0].similarity == pytest.approx(1.0)
    assert summary.semantic_graph[1].similarity == pytest.approx(0.0)


async def test_semantic_graph_skips_chunks_without_embeddings():
    repository = AsyncMock()
    recorder = DocumentIntelligenceRecorder(repository)
    parsed = _parsed_document(DocumentLayout(), OCRMetadata.skipped("skip"))

    chunks = [_chunk(0, embedding=None), _chunk(1, embedding=None)]

    await recorder.save(uuid.uuid4(), parsed, chunks=chunks, chunking_model_id="bge", retrieval_model_id="openai")

    summary = repository.save.call_args[0][0]
    assert summary.semantic_graph == []


async def test_semantic_graph_respects_max_edges():
    repository = AsyncMock()
    recorder = DocumentIntelligenceRecorder(repository, max_graph_edges=2)
    parsed = _parsed_document(DocumentLayout(), OCRMetadata.skipped("skip"))

    chunks = [_chunk(i, embedding=[1.0, float(i)]) for i in range(10)]

    await recorder.save(uuid.uuid4(), parsed, chunks=chunks, chunking_model_id="bge", retrieval_model_id="openai")

    summary = repository.save.call_args[0][0]
    assert len(summary.semantic_graph) == 2
