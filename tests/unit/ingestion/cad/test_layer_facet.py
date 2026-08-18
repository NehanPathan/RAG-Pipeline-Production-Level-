"""Layer as a filter rather than a similarity guess.

A drawing question is structural far more often than it is semantic. "What is
on S-BOLTS" has an exact answer that embedding similarity has to rediscover
from wording, competing against every other chunk in the corpus -- and losing,
because a layer inventory reads nothing like the question.

These tests are about which chunks claim which layers. Claiming too many makes
the filter useless; claiming too few makes it wrong.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.ingestion.loaders.dxf_loader import DxfLoader

pytest.importorskip("ezdxf")

DRAWING = Path(__file__).parents[4] / "tests" / "fixtures" / "cad" / "SSD09.0-02.dxf"

pytestmark = pytest.mark.skipif(not DRAWING.exists(), reason="real DXF fixture not present")


@pytest.fixture(scope="module")
def raw():
    return asyncio.run(DxfLoader().load(DRAWING))


def blocks_naming(raw, layer: str) -> list:
    return [b for b in raw.text_blocks if layer in b.layers]


class TestWhatClaimsALayer:
    def test_a_layer_text_block_claims_exactly_its_own_layer(self, raw):
        block = next(b for b in raw.text_blocks if b.text.startswith("Layer S-TEXT:"))
        assert block.layers == ["S-TEXT"]

    def test_an_annotation_claims_both_ends_of_the_association(self, raw):
        """The callout's text is on S-TEXT and the geometry it points at is on
        S-SECT_STEEL_THRU. A question about either should reach it -- that is
        the whole value of having resolved the association in Phase 1."""
        block = next(
            b
            for b in raw.text_blocks
            if b.element_label == "cad_annotation" and "BENT PLATE" in b.text
        )
        assert set(block.layers) == {"S-TEXT", "S-SECT_STEEL_THRU"}

    def test_the_layer_inventory_claims_every_populated_layer(self, raw):
        block = next(b for b in raw.text_blocks if "Layers present in this drawing" in b.text)
        assert len(block.layers) == 22
        assert "S-BOLTS" in block.layers

    def test_the_symbol_summary_claims_the_layers_it_counts(self, raw):
        block = next(b for b in raw.text_blocks if b.element_label == "cad_symbols")
        assert "S-BOLTS" in block.layers
        assert "S-ARROW_HEAD" in block.layers

    def test_headings_claim_nothing(self, raw):
        """A heading is a boundary marker, not content about a layer."""
        for block in raw.text_blocks:
            if block.element_label == "section_header":
                assert block.layers == []


class TestTheFilterIsUseful:
    def test_asking_for_the_bolts_layer_finds_the_bolt_chunks(self, raw):
        """The question this phase exists for. Before it, `S-BOLTS` appeared
        in one inventory chunk that had to win on embedding similarity against
        the whole corpus."""
        found = blocks_naming(raw, "S-BOLTS")
        assert found, "nothing claims the bolts layer"
        assert any(b.element_label == "cad_symbols" for b in found)

    def test_asking_for_a_geometry_layer_finds_its_callout(self, raw):
        """`S-SECT_STEEL_THRU` carries no text at all, so nothing about it was
        reachable except through the association."""
        found = blocks_naming(raw, "S-SECT_STEEL_THRU")
        assert found
        assert any("BENT PLATE" in b.text for b in found)

    def test_a_layer_that_is_not_on_the_drawing_matches_nothing(self, raw):
        assert blocks_naming(raw, "S-NOT-A-REAL-LAYER") == []

    def test_the_filter_is_selective(self, raw):
        """A field every chunk claims is not a filter. If most blocks named
        most layers this would narrow nothing."""
        claiming_bolts = len(blocks_naming(raw, "S-BOLTS"))
        assert claiming_bolts < len(raw.text_blocks) / 3


class TestItReachesTheChunks:
    def test_layers_survive_chunking(self, raw):
        import uuid

        from src.ingestion.chunkers.chunk_validator import ChunkValidator
        from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
        from src.ingestion.chunkers.parent_child_chunker import (
            ChunkingConfig,
            ParentChildChunker,
        )
        from src.ingestion.chunkers.semantic_chunker import SemanticChunker
        from src.ingestion.chunkers.structure_chunker import StructureChunker
        from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
        from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
        from src.ingestion.ocr.detector import OCRDetector
        from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService

        class _Embedder:
            async def embed_texts(self, texts):
                return [[1.0, 0.0] for _ in texts]

            async def embed_query(self, query):
                return [1.0, 0.0]

            model_id = "stub"
            dimensions = 2

        class _NoOCR:
            async def recognize(self, *args, **kwargs):
                raise RuntimeError("OCR is not part of the CAD path")

        async def build():
            parsed = await DocumentParsingService(
                ocr_detector=OCRDetector(min_words_per_page=10.0),
                ocr_provider=_NoOCR(),
                labeled_analyzer=LabeledLayoutAnalyzer(),
                heuristic_analyzer=HeuristicLayoutAnalyzer(),
            ).process(raw, DRAWING, "dxf")
            pipeline = HybridChunkingPipeline(
                StructureChunker(),
                SemanticChunker(embedding_provider=_Embedder()),
                ParentChildChunker(ChunkingConfig()),
                ChunkValidator(),
            )
            return await pipeline.chunk(uuid.uuid4(), parsed)

        chunks = asyncio.run(build())
        with_layers = [c for c in chunks if c.chunk_metadata.layers]
        assert with_layers, "no chunk reached the end of the pipeline with a layer"
        assert any("S-BOLTS" in c.chunk_metadata.layers for c in chunks)
        assert any("S-SECT_STEEL_THRU" in c.chunk_metadata.layers for c in chunks)
