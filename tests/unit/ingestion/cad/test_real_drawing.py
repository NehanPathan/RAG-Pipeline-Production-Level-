"""The real SSD09.0-02.dxf, end to end through extraction and chunking.

A committed fixture rather than a synthetic one, because the bug this file
exists for could not have been written by hand: an R12 export whose
annotation lives inside anonymous blocks, so model-space reading finds the
disclaimer and the drawing number and nothing an engineer would ask about.

Ground truth, established with ezdxf directly (see the audit in the commit
message): 418 model-space entities across 22 populated layers, 45 non-empty
text entities of which 35 sit inside block definitions. Before the fix, 10
were extracted -- 22%.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from src.ingestion.cad.dxf_reader import DxfReader
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor
from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.dxf_loader import DxfLoader
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.parsing.drawing_detector import ContentKind
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService

pytest.importorskip("ezdxf")

DRAWING = Path(__file__).parents[4] / "tests" / "fixtures" / "cad" / "SSD09.0-02.dxf"

pytestmark = pytest.mark.skipif(not DRAWING.exists(), reason="real DXF fixture not present")


class _StubEmbedder:
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


class _NoOCR:
    async def recognize(self, *args, **kwargs):
        raise RuntimeError("OCR is not part of the CAD path")


@pytest.fixture(scope="module")
def cad():
    return DxfReader().read(DRAWING)


@pytest.fixture(scope="module")
def raw(cad):
    import asyncio

    return asyncio.run(DxfLoader().load(DRAWING))


@pytest.fixture(scope="module")
def chunks(raw):
    import asyncio

    async def _build():
        parsed = await DocumentParsingService(
            ocr_detector=OCRDetector(min_words_per_page=10.0),
            ocr_provider=_NoOCR(),
            labeled_analyzer=LabeledLayoutAnalyzer(),
            heuristic_analyzer=HeuristicLayoutAnalyzer(),
        ).process(raw, DRAWING, "dxf")
        pipeline = HybridChunkingPipeline(
            StructureChunker(),
            SemanticChunker(embedding_provider=_StubEmbedder()),
            ParentChildChunker(ChunkingConfig()),
            ChunkValidator(),
            entity_extractor=RegexSteelEntityExtractor(),
        )
        return parsed, await pipeline.chunk(uuid.uuid4(), parsed)

    return asyncio.run(_build())


def _all_text(chunk_list) -> str:
    return "\n".join(c.content for c in chunk_list).upper()


class TestExtraction:
    def test_text_inside_blocks_is_read(self, cad):
        """The bug. Model space alone yields 10 of 45 non-empty texts."""
        assert len(cad.texts) >= 30

    def test_the_bolt_specification_is_extracted(self, cad):
        """`ALL BOLTS 3/4" DIA. A325` sits inside block *U8. It is the only
        statement of bolt spec in the drawing, and it was invisible."""
        joined = " ".join(t.text for t in cad.texts).upper()
        assert "A325" in joined
        assert "3/4" in joined
        assert "BOLT" in joined

    def test_member_callouts_are_extracted(self, cad):
        joined = " ".join(t.text for t in cad.texts).upper()
        for expected in ("WF COLUMN", "BEAM", "PURLIN"):
            assert expected in joined, expected

    def test_section_sizes_are_extracted(self, cad):
        joined = " ".join(t.text for t in cad.texts).upper()
        assert "L3 1/2" in joined, "the angle callout"
        assert "BENT PLATE" in joined

    def test_layer_entity_counts_are_recorded(self, cad):
        """A layer carrying only geometry leaves no text, so counting what is
        drawn on it is the only way it can be described at all."""
        assert "S-BOLTS" in cad.entities_per_layer
        assert sum(cad.entities_per_layer["S-BOLTS"].values()) > 0
        assert "S-SECT_STEEL" in cad.entities_per_layer

    def test_recursion_is_bounded(self):
        """A block that references itself must not recurse forever."""
        reader = DxfReader(max_block_depth=2)
        assert reader.read(DRAWING).texts


class TestRepresentation:
    def test_the_drawing_is_cad_native(self, chunks):
        parsed, _ = chunks
        assert parsed.content_kind is ContentKind.CAD_NATIVE
        assert parsed.content_kind.has_exact_dimensions

    def test_every_chunk_is_labelled_cad_native(self, chunks):
        _, chunk_list = chunks
        assert chunk_list
        assert all(c.chunk_metadata.content_kind == "cad_native" for c in chunk_list)

    def test_a_layer_inventory_chunk_exists(self, chunks):
        """ "What layers are present" needs evidence, not a relaxed guard."""
        _, chunk_list = chunks
        inventory = [c for c in chunk_list if "LAYERS PRESENT" in c.content.upper()]
        assert inventory, "no chunk inventories the drawing's layers"
        text = inventory[0].content.upper()
        for layer in ("S-BOLTS", "S-SECT_STEEL", "S-DIMS", "S-TEXT"):
            assert layer in text, layer

    def test_layer_text_chunks_name_their_layer(self, chunks):
        """A fragment reading `3"` twice is unusable without its layer."""
        _, chunk_list = chunks
        assert any(c.content.startswith("Layer ") for c in chunk_list)

    def test_chunks_carry_a_page_number(self, chunks):
        _, chunk_list = chunks
        assert all(c.chunk_metadata.page_number is not None for c in chunk_list)


class TestTheQuestionsThatFailed:
    """The five questions asked of the real drawing, as content assertions.

    Retrieval and generation are exercised live; what these pin is that the
    evidence each question needs is present in the indexed text at all --
    which is what was actually missing.
    """

    def test_bolt_evidence_is_present(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "A325" in text
        assert "BOLT" in text

    def test_steel_evidence_is_present(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        for expected in ("WF COLUMN", "BEAM", "PURLIN", "BENT PLATE"):
            assert expected in text, expected

    def test_layer_evidence_is_present(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "S-BOLTS" in text
        assert "S-SECT_STEEL" in text

    def test_drawing_identity_is_present(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "SSD09.0-02" in text
        assert "V1.1" in text

    def test_no_dimension_is_invented(self, cad):
        """The file holds zero native DIMENSION entities. Whatever the sheet
        shows as dimensions is drawn as lines and text, so the dimension
        schedule must stay empty rather than fabricate measurements."""
        assert cad.dimensions == []
