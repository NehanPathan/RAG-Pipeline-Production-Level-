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
