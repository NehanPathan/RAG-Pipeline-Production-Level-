import uuid

from src.domain.entities.conversation import Citation
from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.answer.context_assembler import ContextAssembler


def _compressed(content, chunk_id=None, token_count=10):
    chunk = DocumentChunk(
        id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
    )
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    reranked = RerankedChunk(fused=fused, rerank_score=0.9)
    return CompressedChunk(reranked=reranked, compressed_content=content, token_count=token_count)


def _citation(chunk_id, index, document_name="policy.pdf", page_number=None):
    return Citation(
        index=index,
        source_name=document_name,
        document_name=document_name,
        chunk_id=chunk_id,
        page_number=page_number,
    )


def test_assemble_includes_citation_markers_and_content():
    chunk_id = uuid.uuid4()
    chunk = _compressed("relevant excerpt", chunk_id=chunk_id)
    citations = {chunk_id: _citation(chunk_id, index=1, document_name="policy.pdf", page_number=5)}
    assembler = ContextAssembler()

    result = assembler.assemble([chunk], citations)

    assert "[1]" in result.formatted_text
    assert "policy.pdf" in result.formatted_text
    assert "p.5" in result.formatted_text
    assert "relevant excerpt" in result.formatted_text


def test_assemble_sums_token_counts():
    a = _compressed("a", token_count=10)
    b = _compressed("b", token_count=15)
    assembler = ContextAssembler()

    result = assembler.assemble([a, b], {})

    assert result.total_tokens == 25


def test_assemble_handles_missing_citation_gracefully():
    chunk = _compressed("orphan chunk")
    assembler = ContextAssembler()

    result = assembler.assemble([chunk], {})

    assert "[?]" in result.formatted_text
    assert "Unknown source" in result.formatted_text


def test_assemble_passes_through_citations_dict():
    chunk_id = uuid.uuid4()
    chunk = _compressed("a", chunk_id=chunk_id)
    citations = {chunk_id: _citation(chunk_id, index=1)}
    assembler = ContextAssembler()

    result = assembler.assemble([chunk], citations)

    assert result.citations == citations


def test_assemble_empty_chunks_returns_empty_text():
    assembler = ContextAssembler()

    result = assembler.assemble([], {})

    assert result.formatted_text == ""
    assert result.total_tokens == 0
