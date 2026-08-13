import uuid
from pathlib import Path

from src.domain.entities.document import ChunkType
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock
from src.ingestion.ocr.models import OCRMetadata
from src.ingestion.parsing.drawing_detector import ContentClassification, ContentKind
from src.ingestion.parsing.parsed_document import ParsedDocument


class StubEmbeddingProvider:
    async def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, query):
        return [1.0, 0.0]

    @property
    def model_id(self) -> str:
        return "stub"

    @property
    def dimensions(self) -> int:
        return 2


def _pipeline(min_ocr_confidence: float = 0.0) -> HybridChunkingPipeline:
    return HybridChunkingPipeline(
        structure_chunker=StructureChunker(),
        semantic_chunker=SemanticChunker(
            embedding_provider=StubEmbeddingProvider(), min_sentences_for_split=999
        ),
        parent_child_chunker=ParentChildChunker(
            ChunkingConfig(parent_chunk_size=64, child_chunk_size=16, overlap=4)
        ),
        validator=ChunkValidator(min_ocr_confidence=min_ocr_confidence),
    )


def _parsed(blocks, tables=None, ocr_metadata=None) -> ParsedDocument:
    raw = RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=blocks,
        tables=tables or [],
        page_count=1,
    )
    return ParsedDocument(
        raw=raw, ocr_metadata=ocr_metadata or OCRMetadata.skipped("test"), layout=DocumentLayout()
    )


async def test_produces_parent_child_and_table_chunks():
    pipeline = _pipeline()
    text = " ".join(f"word{i}" for i in range(100))
    doc = _parsed(
        [
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=1, page_number=1),
            TextBlock(text=text, element_label="text", page_number=1),
        ],
        tables=[TableBlock(markdown="| a | b |", page_number=2, row_count=1, col_count=2)],
    )
    chunks = await pipeline.chunk(uuid.uuid4(), doc)

    assert any(c.chunk_type == ChunkType.PARENT for c in chunks)
    assert any(c.chunk_type == ChunkType.CHILD for c in chunks)
    assert any(c.chunk_type == ChunkType.TABLE for c in chunks)


async def test_chunk_metadata_carries_section_title_and_heading_level():
    pipeline = _pipeline()
    doc = _parsed(
        [
            TextBlock(text="Chapter 1", element_label="section_header", heading_level=2, page_number=1),
            TextBlock(
                text=" ".join(f"word{i}" for i in range(50)), element_label="text", page_number=1
            ),
        ]
    )
    chunks = await pipeline.chunk(uuid.uuid4(), doc)
    assert all(c.chunk_metadata.section_title == "Chapter 1" for c in chunks)
    assert all(c.chunk_metadata.heading_level == 2 for c in chunks)


async def test_validator_drops_garbage_chunks():
    pipeline = _pipeline()
    doc = _parsed(
        [TextBlock(text="%%%$$$###@@@&&&***!!!^^^~~~", element_label="text", page_number=1)]
    )
    chunks = await pipeline.chunk(uuid.uuid4(), doc)
    assert chunks == []


async def test_low_ocr_confidence_drops_all_chunks():
    pipeline = _pipeline(min_ocr_confidence=0.9)
    doc = _parsed(
        [TextBlock(text=" ".join(f"word{i}" for i in range(50)), element_label="text", page_number=1)],
        ocr_metadata=OCRMetadata(engine="tesseract", ran=True, confidence=0.2),
    )
    chunks = await pipeline.chunk(uuid.uuid4(), doc)
    assert chunks == []


async def test_positions_are_sequential_and_unique_across_segments_and_tables():
    pipeline = _pipeline()
    doc = _parsed(
        [TextBlock(text=" ".join(f"word{i}" for i in range(50)), element_label="text", page_number=1)],
        tables=[TableBlock(markdown="| a |", page_number=1, row_count=1, col_count=1)],
    )
    chunks = await pipeline.chunk(uuid.uuid4(), doc)
    positions = [c.position for c in chunks]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)


# --- Drawing routing -------------------------------------------------------
#
# A drawing scans far worse than prose -- rotated dimension text, hatching
# and leader lines drag the mean confidence down -- so it is validated
# against a lower floor. Which floor applies used to be decided from the
# loader name, and only `image_passthrough` qualified. A drawing exported to
# PDF, which is how drawings actually arrive, loads through Docling like any
# report and was held to the prose floor. That is the same failure as the
# original bug it was written to fix: the drawings most in need of the lower
# floor were the ones that never got it.


def _drawing_parsed(kind: ContentKind, loader_name: str = "docling") -> ParsedDocument:
    """A parsed document that OCR ran on, classified as `kind`."""
    raw = RawDocument(
        file_path=Path("/tmp/S-104.pdf"),
        file_name="S-104.pdf",
        mime_type="application/pdf",
        text_blocks=[TextBlock(text="DRAWING NO: S-104", page_number=1)],
        tables=[],
        page_count=1,
        loader_name=loader_name,
    )
    return ParsedDocument(
        raw=raw,
        ocr_metadata=OCRMetadata(engine="tesseract", ran=True, confidence=0.25),
        layout=DocumentLayout(),
        classification=ContentClassification(kind=kind, reason="test"),
    )


def test_pdf_drawings_get_the_drawing_ocr_floor():
    """The gap. Both PDF drawing cases load through Docling, not as images."""
    pipeline = _pipeline()

    for kind in (ContentKind.VECTOR_DRAWING, ContentKind.SCANNED_DRAWING):
        assert pipeline._is_drawing(_drawing_parsed(kind)) is True, kind


def test_a_dxf_is_a_drawing_too():
    assert _pipeline()._is_drawing(_drawing_parsed(ContentKind.CAD_NATIVE, "dxf")) is True


def test_a_scanned_report_keeps_the_prose_floor():
    """Not everything that is scanned is a drawing.

    A scanned specification is laid out as prose and should OCR cleanly, so
    a low confidence there is a real problem rather than the nature of the
    input -- it must not be waved through by the drawing floor.
    """
    assert _pipeline()._is_drawing(_drawing_parsed(ContentKind.SCANNED_PROSE)) is False
    assert _pipeline()._is_drawing(_drawing_parsed(ContentKind.PROSE)) is False


def test_an_unclassified_image_document_still_gets_the_drawing_floor():
    """The pre-existing loader-name rule survives as a fallback.

    A ParsedDocument built without DocumentParsingService carries the
    default classification, and must not silently lose the behaviour it had.
    """
    unclassified = ParsedDocument(
        raw=RawDocument(
            file_path=Path("/tmp/scan.png"),
            file_name="scan.png",
            mime_type="image/png",
            text_blocks=[TextBlock(text="B-14 ISMB 300", page_number=1)],
            tables=[],
            page_count=1,
            loader_name="image_passthrough",
        ),
        ocr_metadata=OCRMetadata(engine="tesseract", ran=True, confidence=0.25),
        layout=DocumentLayout(),
    )

    assert _pipeline()._is_drawing(unclassified) is True


def test_the_drawing_floor_only_applies_when_ocr_actually_ran():
    """A vector drawing's text is extracted, not recognised.

    There is no confidence figure to judge, so nothing should be relaxed.
    """
    vector = _drawing_parsed(ContentKind.VECTOR_DRAWING)
    vector.ocr_metadata = OCRMetadata.skipped("native text layer")

    assert _pipeline()._is_drawing(vector) is False


async def test_a_badly_scanned_drawing_still_produces_chunks():
    """The end of the chain: classification -> lower floor -> chunks survive.

    At 0.25 mean confidence -- ordinary for a scanned sheet -- the prose
    floor of 0.35 rejects every chunk and the document reports
    `status=indexed` with `chunks_created=0`.
    """
    pipeline = HybridChunkingPipeline(
        structure_chunker=StructureChunker(),
        semantic_chunker=SemanticChunker(
            embedding_provider=StubEmbeddingProvider(), min_sentences_for_split=999
        ),
        parent_child_chunker=ParentChildChunker(
            ChunkingConfig(parent_chunk_size=64, child_chunk_size=16, overlap=4)
        ),
        validator=ChunkValidator(min_ocr_confidence=0.35, min_ocr_confidence_drawing=0.15),
    )

    drawing = _drawing_parsed(ContentKind.SCANNED_DRAWING)
    drawing.raw.text_blocks = [
        TextBlock(text="B-14 ISMB 300 Fe 415 SPAN 6000 SHEAR 180 kN", page_number=1)
    ]
    prose = _drawing_parsed(ContentKind.SCANNED_PROSE)
    prose.raw.text_blocks = [
        TextBlock(text="B-14 ISMB 300 Fe 415 SPAN 6000 SHEAR 180 kN", page_number=1)
    ]

    drawing_chunks = await pipeline.chunk(uuid.uuid4(), drawing)
    prose_chunks = await pipeline.chunk(uuid.uuid4(), prose)

    assert drawing_chunks, "a scanned drawing at 0.25 confidence must still index"
    # Same text, same confidence -- only the classification differs.
    assert prose_chunks == []


async def test_every_chunk_records_how_its_text_was_obtained():
    """An answer quoting a dimension has to be able to say where it came from."""
    pipeline = _pipeline()
    doc = _drawing_parsed(ContentKind.CAD_NATIVE, "dxf")
    doc.raw.text_blocks = [
        TextBlock(text="B-14 ISMB 300 Fe 415 SPAN 6000 SHEAR 180 kN", page_number=1)
    ]

    chunks = await pipeline.chunk(uuid.uuid4(), doc)

    assert chunks
    assert all(c.chunk_metadata.content_kind == "cad_native" for c in chunks)
