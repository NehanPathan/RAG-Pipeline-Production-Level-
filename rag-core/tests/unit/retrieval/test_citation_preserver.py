import uuid

from src.domain.entities.document import ChunkMetadata, DocumentChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.citation_preserver import CitationPreserver


def _compressed(content, document_name=None, page_number=None, section=None, chunk_id=None):
    chunk = DocumentChunk(
        id=chunk_id or uuid.uuid4(),
        document_id=uuid.uuid4(),
        content=content,
        position=0,
        document_name=document_name,
        chunk_metadata=ChunkMetadata(page_number=page_number, section=section),
    )
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    reranked = RerankedChunk(fused=fused, rerank_score=0.9)
    return CompressedChunk(reranked=reranked, compressed_content=content)


def test_build_assigns_sequential_indices():
    chunks = [_compressed("a"), _compressed("b")]
    preserver = CitationPreserver()

    citations = preserver.build(chunks)

    indices = sorted(c.index for c in citations.values())
    assert indices == [1, 2]


def test_build_includes_document_metadata():
    chunk_id = uuid.uuid4()
    chunks = [
        _compressed("a", document_name="policy.pdf", page_number=5, section="3.2", chunk_id=chunk_id)
    ]
    preserver = CitationPreserver()

    citations = preserver.build(chunks)

    citation = citations[chunk_id]
    assert citation.document_name == "policy.pdf"
    assert citation.source_name == "policy.pdf"
    assert citation.page_number == 5
    assert citation.section == "3.2"


def test_build_falls_back_for_missing_document_name():
    chunk_id = uuid.uuid4()
    chunks = [_compressed("a", document_name=None, chunk_id=chunk_id)]
    preserver = CitationPreserver()

    citations = preserver.build(chunks)

    assert citations[chunk_id].document_name == "Unknown source"


def test_build_empty_list_returns_empty_dict():
    assert CitationPreserver().build([]) == {}
