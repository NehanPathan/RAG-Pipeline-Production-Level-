import uuid
from pathlib import Path

from src.ingestion.chunkers.chunking_strategy import ParentChildOnlyStrategy
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument, TextBlock
from src.ingestion.ocr.models import OCRMetadata
from src.ingestion.parsing.parsed_document import ParsedDocument


async def test_parent_child_only_strategy_delegates_to_existing_chunker():
    parent_child_chunker = ParentChildChunker(
        ChunkingConfig(parent_chunk_size=32, child_chunk_size=8, overlap=2)
    )
    strategy = ParentChildOnlyStrategy(parent_child_chunker)

    text = " ".join(f"word{i}" for i in range(80))
    raw = RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=[TextBlock(text=text, page_number=1)],
        page_count=1,
    )
    parsed = ParsedDocument(raw=raw, ocr_metadata=OCRMetadata.skipped("test"), layout=DocumentLayout())
    document_id = uuid.uuid4()

    strategy_chunks = await strategy.chunk(document_id, parsed)
    direct_chunks = parent_child_chunker.chunk(document_id, raw)

    assert len(strategy_chunks) == len(direct_chunks)
    assert [c.content for c in strategy_chunks] == [c.content for c in direct_chunks]
