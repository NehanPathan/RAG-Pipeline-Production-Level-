import uuid

import pytest

from src.domain.entities.document import ChunkType
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.loaders.base import RawDocument, TableBlock, TextBlock
from pathlib import Path


@pytest.fixture
def doc_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def small_chunker() -> ParentChildChunker:
    return ParentChildChunker(ChunkingConfig(parent_chunk_size=128, child_chunk_size=32, overlap=4))


@pytest.fixture
def raw_doc() -> RawDocument:
    long_text = " ".join([f"word{i}" for i in range(200)])
    return RawDocument(
        file_path=Path("/tmp/test.pdf"),
        file_name="test.pdf",
        mime_type="application/pdf",
        text_blocks=[TextBlock(text=long_text, page_number=1)],
        tables=[
            TableBlock(markdown="| A | B |\n|---|---|\n| 1 | 2 |", page_number=1, row_count=2, col_count=2)
        ],
        page_count=1,
    )


def test_produces_parent_and_child_chunks(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    parents = [c for c in chunks if c.chunk_type == ChunkType.PARENT]
    children = [c for c in chunks if c.chunk_type == ChunkType.CHILD]

    assert len(parents) >= 1
    assert len(children) >= 1


def test_child_chunks_reference_parent(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    parents = {c.id for c in chunks if c.chunk_type == ChunkType.PARENT}
    children = [c for c in chunks if c.chunk_type == ChunkType.CHILD]

    for child in children:
        assert child.parent_chunk_id is not None
        assert child.parent_chunk_id in parents


def test_table_chunks_created(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE]
    assert len(table_chunks) == 1
    assert table_chunks[0].chunk_metadata.contains_table is True


def test_all_chunks_have_document_id(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    assert all(c.document_id == doc_id for c in chunks)


def test_positions_are_sequential(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    positions = [c.position for c in chunks]
    assert positions == list(range(len(chunks)))


def test_token_counts_populated(small_chunker, doc_id, raw_doc):
    chunks = small_chunker.chunk(doc_id, raw_doc)
    assert all(c.token_count > 0 for c in chunks)


def test_empty_document_produces_no_crash(small_chunker, doc_id):
    empty_doc = RawDocument(
        file_path=Path("/tmp/empty.pdf"),
        file_name="empty.pdf",
        mime_type="application/pdf",
        text_blocks=[],
        tables=[],
        page_count=0,
    )
    chunks = small_chunker.chunk(doc_id, empty_doc)
    assert isinstance(chunks, list)


def test_chunk_section_produces_parent_and_child_chunks(small_chunker, doc_id):
    text = " ".join(f"word{i}" for i in range(200))
    chunks = small_chunker.chunk_section(doc_id, text, page_number=3, section_title="Chapter 1")
    parents = [c for c in chunks if c.chunk_type == ChunkType.PARENT]
    children = [c for c in chunks if c.chunk_type == ChunkType.CHILD]
    assert len(parents) >= 1
    assert len(children) >= 1
    assert all(c.chunk_metadata.page_number == 3 for c in chunks)
    assert all(c.chunk_metadata.section_title == "Chapter 1" for c in chunks)


def test_chunk_section_respects_start_position(small_chunker, doc_id):
    chunks = small_chunker.chunk_section(doc_id, "short text", start_position=42)
    assert chunks[0].position == 42


def test_chunk_section_does_not_mutate_chunk_method(small_chunker, doc_id, raw_doc):
    # chunk_section is additive -- calling it must not change chunk()'s output.
    before = small_chunker.chunk(doc_id, raw_doc)
    small_chunker.chunk_section(doc_id, "some other section text " * 50)
    after = small_chunker.chunk(doc_id, raw_doc)
    assert len(before) == len(after)
