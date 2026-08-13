"""A five-page PDF that is five different things, end to end.

Real Docling, real chunking, real embeddings, real Qdrant and Elasticsearch
-- because the thing under test is precisely what a stub would paper over:
Docling silently OCRs scanned pages and returns the result as ordinary text,
so a mocked loader cannot reproduce the case that matters.

The document holds, in order: a specification page, a beam schedule parsed
as a table, a plotted CAD sheet, a scan of that same sheet, and a page with
a detail drawing above and erection notes below. Classified as one document
it came back `vector_drawing` -- wrong for four pages, and with the scanned
page averaged out of existence so it never reached OCR.
"""

from __future__ import annotations

import uuid

import pytest

from src.domain.entities.document import ChunkType
from src.domain.repositories.search_repository import BM25SearchFilter
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor
from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.docling_loader import DoclingLoader
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.parsing.drawing_detector import ContentKind
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService

pytestmark = pytest.mark.integration

EXPECTED_PAGE_KINDS = {
    1: ContentKind.PROSE,
    2: ContentKind.PROSE,
    3: ContentKind.VECTOR_DRAWING,
    4: ContentKind.SCANNED_DRAWING,
    5: ContentKind.MIXED,
}


class _StubEmbedder:
    """Deterministic vectors. Embedding quality is not what this verifies."""

    async def embed_texts(self, texts):
        return [[float(len(t) % 7), 1.0] for t in texts]

    async def embed_query(self, query):
        return [float(len(query) % 7), 1.0]

    @property
    def model_id(self) -> str:
        return "stub-2d"

    @property
    def dimensions(self) -> int:
        return 2


@pytest.fixture(scope="module")
async def ingested(mixed_document_pdf):
    """Load, classify, OCR-route and chunk the five-page document for real."""
    raw = await DoclingLoader().load(mixed_document_pdf)

    class _NoOpOCR:
        """OCR needs poppler/tesseract binaries this suite does not require.

        The parsing service falls back to the loader's text when OCR fails,
        which is the behaviour a host without them gets -- and the
        classification, which is what is under test, does not depend on OCR
        having succeeded.
        """

        async def recognize(self, *args, **kwargs):
            raise RuntimeError("OCR binaries not available in this environment")

    parsed = await DocumentParsingService(
        ocr_detector=OCRDetector(min_words_per_page=10.0),
        ocr_provider=_NoOpOCR(),
        labeled_analyzer=LabeledLayoutAnalyzer(),
        heuristic_analyzer=HeuristicLayoutAnalyzer(),
    ).process(raw, mixed_document_pdf, "pdf")

    pipeline = HybridChunkingPipeline(
        structure_chunker=StructureChunker(),
        semantic_chunker=SemanticChunker(embedding_provider=_StubEmbedder()),
        parent_child_chunker=ParentChildChunker(ChunkingConfig()),
        validator=ChunkValidator(),
        entity_extractor=RegexSteelEntityExtractor(),
    )
    document_id = uuid.uuid4()
    chunks = await pipeline.chunk(document_id, parsed)
    return parsed, document_id, chunks


class TestClassification:
    async def test_each_page_keeps_its_own_kind(self, ingested):
        parsed, _, _ = ingested

        actual = {n: p.kind for n, p in sorted(parsed.classification.pages.items())}

        assert actual == EXPECTED_PAGE_KINDS

    async def test_the_scanned_page_is_found_among_native_ones(self, ingested):
        """Docling OCR'd page 4 internally and returned ~30 words, so word
        density saw a healthy page. Only the per-page text-layer probe sees
        that those words are not in the file."""
        parsed, _, _ = ingested

        assert parsed.classification.pages_without_text_layer == [4]

    async def test_ocr_was_routed_to_that_page_only(self, ingested):
        """Four good pages are not re-OCR'd because the fifth was a scan."""
        parsed, _, _ = ingested
        decision = OCRDetector(min_words_per_page=10.0).detect(
            parsed.raw, "pdf", pages_without_text_layer=[4]
        )

        assert decision.required is True
        assert 4 in decision.pages_required
        assert 1 not in decision.pages_required


class TestExtraction:
    async def test_every_page_produced_chunks(self, ingested):
        """Nothing is dropped by being the wrong kind of content."""
        _, _, chunks = ingested

        pages = {c.chunk_metadata.page_number for c in chunks}

        assert {1, 2, 3, 4, 5} <= pages

    async def test_the_table_page_produced_a_table_chunk(self, ingested):
        _, _, chunks = ingested

        tables = [c for c in chunks if c.chunk_type is ChunkType.TABLE]

        assert tables
        assert any(c.chunk_metadata.page_number == 2 for c in tables)

    async def test_steel_entities_were_found_on_drawing_and_prose_pages(self, ingested):
        _, _, chunks = ingested

        canonicals = {
            e["canonical"] for c in chunks for e in c.chunk_metadata.entities if e.get("canonical")
        }

        assert "ISMB 300" in canonicals
        assert any(c.startswith("IS ") for c in canonicals), canonicals


class TestChunkLabelling:
    async def test_chunks_are_labelled_from_their_own_page(self, ingested):
        _, _, chunks = ingested

        by_page: dict[int, set[str]] = {}
        for chunk in chunks:
            page = chunk.chunk_metadata.page_number
            if page is not None:
                by_page.setdefault(page, set()).add(chunk.chunk_metadata.content_kind or "")

        assert by_page[1] == {"prose"}
        assert by_page[3] == {"vector_drawing"}
        assert by_page[4] == {"scanned_drawing"}

    async def test_a_mixed_page_labels_each_chunk_by_its_own_text(self, ingested):
        """The title block is drawing content; the erection notes are not.

        Both sit on page 5, and forcing the page into one type would
        mislabel whichever half lost.
        """
        _, _, chunks = ingested

        page_five = {
            c.chunk_metadata.content_kind for c in chunks if c.chunk_metadata.page_number == 5
        }

        assert "vector_drawing" in page_five
        assert "prose" in page_five
        assert "mixed" not in page_five, "MIXED describes a page, never a chunk"

    async def test_no_chunk_claims_exact_dimensions(self, ingested):
        """Every page here is a picture of geometry. Only a DXF is not."""
        _, _, chunks = ingested

        kinds = {c.chunk_metadata.content_kind for c in chunks}

        assert "cad_native" not in kinds


class TestSearchable:
    async def test_chunks_are_indexed_and_retrievable_with_their_page_and_kind(
        self, ingested, search_repository
    ):
        """Citations need the page number and the provenance to survive the
        round-trip into Elasticsearch and back."""
        _, document_id, chunks = ingested
        await search_repository.index_batch(chunks)

        results = await search_repository.search(
            "ISMB", top_k=50, filters=BM25SearchFilter(document_ids=[document_id])
        )

        assert results
        for hit in results:
            assert hit.chunk.chunk_metadata.page_number is not None
            assert hit.chunk.chunk_metadata.content_kind is not None

    async def test_drawing_chunks_can_be_filtered_apart_from_prose(
        self, ingested, search_repository
    ):
        """ "Only the drawings in this document" is a filter, not a guess."""
        _, document_id, chunks = ingested
        await search_repository.index_batch(chunks)

        drawings = await search_repository.search(
            "*",
            top_k=50,
            filters=BM25SearchFilter(
                document_ids=[document_id],
                content_kinds=["vector_drawing", "scanned_drawing"],
            ),
        )

        assert drawings
        assert {h.chunk.chunk_metadata.content_kind for h in drawings} <= {
            "vector_drawing",
            "scanned_drawing",
        }
        assert {h.chunk.chunk_metadata.page_number for h in drawings} & {3, 4, 5}

    async def test_content_kind_is_offered_as_a_facet(self, ingested, search_repository):
        _, document_id, chunks = ingested
        await search_repository.index_batch(chunks)

        facets = await search_repository.aggregate_facets(
            ["content_kind"], filters=BM25SearchFilter(document_ids=[document_id])
        )

        values = {value for value, _ in facets["content_kind"]}
        assert {"prose", "vector_drawing", "scanned_drawing"} <= values

    async def test_a_filter_only_search_returns_the_matching_chunks(
        self, ingested, search_repository
    ):
        """ "Everything in this document" is a filter, not a question.

        The structured-search endpoint sends `*` when the user typed no
        query. `match` on `*` looks for the literal token, which occurs in
        no document, so filter-only browsing silently returned nothing --
        the exact case a drawing register is used for.
        """
        _, document_id, chunks = ingested
        await search_repository.index_batch(chunks)

        for query in ("*", ""):
            results = await search_repository.search(
                query, top_k=50, filters=BM25SearchFilter(document_ids=[document_id])
            )

            assert len(results) == len(chunks), query
