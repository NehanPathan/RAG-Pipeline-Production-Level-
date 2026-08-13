import uuid
from unittest.mock import AsyncMock

import pytest

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.retrieval.context.context_compressor import ContextCompressor

# Long enough to be worth compressing. Below `min_chars_to_compress` the
# compressor keeps the chunk verbatim and never calls the LLM, so a test
# using a short string would be exercising the short-circuit instead.
LONG = (
    "This specification covers the supply and erection of structural steelwork. "
    "All steel shall conform to IS 2062 E250 unless noted otherwise on the drawings. "
    "Bolted connections shall use property class 8.8 bolts to IS 1367. "
    "Welding shall be carried out by qualified welders in accordance with IS 816. "
)


def _reranked(content, chunk_id=None):
    chunk = DocumentChunk(
        id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), content=content, position=0
    )
    fused = FusedChunk(chunk=chunk, rrf_score=0.01)
    return RerankedChunk(fused=fused, rerank_score=0.9)


@pytest.fixture
def compressor(mock_llm_provider):
    return ContextCompressor(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_compress_returns_llm_extracted_content(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="relevant excerpt only")
    chunks = [_reranked(LONG)]

    result = await compressor.compress("query", chunks)

    assert len(result) == 1
    assert result[0].compressed_content == "relevant excerpt only"


@pytest.mark.asyncio
async def test_compress_drops_chunk_when_llm_returns_none(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="NONE")
    chunks = [_reranked(LONG)]

    result = await compressor.compress("query", chunks)

    assert result == []


@pytest.mark.asyncio
async def test_compress_falls_back_to_original_content_on_llm_exception(
    compressor, mock_llm_provider
):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    chunks = [_reranked("original content")]

    result = await compressor.compress("query", chunks)

    assert len(result) == 1
    assert result[0].compressed_content == "original content"


@pytest.mark.asyncio
async def test_compress_runs_all_chunks(compressor, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="compressed")
    chunks = [_reranked(LONG + "a"), _reranked(LONG + "b"), _reranked(LONG + "c")]

    result = await compressor.compress("query", chunks)

    assert len(result) == 3
    assert mock_llm_provider.complete.call_count == 3


@pytest.mark.asyncio
async def test_compress_empty_list_returns_empty(compressor):
    result = await compressor.compress("query", [])

    assert result == []


# --- Short chunks are kept verbatim ----------------------------------------
#
# Compression trims a long passage down to its relevant part. A short chunk
# has no fat, and the call stops being "extract the relevant sentences" and
# becomes "judge whether this is relevant at all" -- which this prompt
# answers badly, and badly in one direction.
#
# A drawing chunk is `DRAWING NO: SSD09.0-01 / REV: A`. It contains no
# sentences, so a prompt asking for "the sentences that are relevant"
# returned NONE and every chunk of a CAD sheet was discarded here. Retrieval
# reported seven results and the grounding gate then refused for want of
# context: "I can't answer that from the indexed documents", about a drawing
# that was indexed, correct, and sitting in the results.


@pytest.mark.asyncio
async def test_a_title_block_survives_compression(compressor, mock_llm_provider):
    """The bug. This chunk is the answer to "which drawing is this"."""
    mock_llm_provider.complete = AsyncMock(return_value="NONE")

    result = await compressor.compress(
        "What is this drawing about?", [_reranked("DRAWING NO: SSD09.0-01\nREV: A")]
    )

    assert len(result) == 1
    assert "SSD09.0-01" in result[0].compressed_content


@pytest.mark.asyncio
async def test_a_short_chunk_costs_no_llm_call(compressor, mock_llm_provider):
    """Cheaper as well as more correct: there is nothing to ask about."""
    mock_llm_provider.complete = AsyncMock(return_value="NONE")

    await compressor.compress("query", [_reranked("BEAM B-14 ISMB 300 Fe 415")])

    mock_llm_provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_a_long_chunk_is_still_compressed(compressor, mock_llm_provider):
    """The short-circuit must not disable compression generally -- that is
    what keeps a prose corpus inside its token budget."""
    mock_llm_provider.complete = AsyncMock(return_value="the relevant sentence")

    result = await compressor.compress("query", [_reranked(LONG)])

    mock_llm_provider.complete.assert_called_once()
    assert result[0].compressed_content == "the relevant sentence"


@pytest.mark.asyncio
async def test_the_threshold_is_configurable(mock_llm_provider):
    """A deployment whose corpus is all short text can turn this off."""
    mock_llm_provider.complete = AsyncMock(return_value="NONE")
    compressor = ContextCompressor(llm_provider=mock_llm_provider, min_chars_to_compress=0)

    result = await compressor.compress("query", [_reranked("DRAWING NO: S-104")])

    mock_llm_provider.complete.assert_called_once()
    assert result == []


@pytest.mark.asyncio
async def test_a_mixed_set_keeps_the_drawing_and_compresses_the_prose(
    compressor, mock_llm_provider
):
    """The realistic case: a sheet's title block retrieved beside a
    specification paragraph."""
    mock_llm_provider.complete = AsyncMock(return_value="steel conforms to IS 2062")

    result = await compressor.compress(
        "what grade of steel", [_reranked("DRAWING NO: SSD09.0-01"), _reranked(LONG)]
    )

    contents = [c.compressed_content for c in result]
    assert "DRAWING NO: SSD09.0-01" in contents
    assert "steel conforms to IS 2062" in contents
