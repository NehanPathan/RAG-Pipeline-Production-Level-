"""Escalation decisions against the real drawing's own chunks.

The unit tests next door use hand-written passages. These use what
`SSD09.0-02.dxf` and the schedule fixture actually produce, because the
escalation gate keys off phrases the earlier phases emit -- "could not be
determined", "unnamed shape" -- and a test that invents those phrases would
keep passing after the loader stopped writing them.

Nothing here calls a model. What is under test is which questions would, and
the answer is almost none of them.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.dxf_loader import DxfLoader
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService
from src.retrieval.vision.escalation import decide
from src.retrieval.vision.models import EscalationReason

pytest.importorskip("ezdxf")

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "cad"
DETAIL = FIXTURES / "SSD09.0-02.dxf"
SCHEDULE = FIXTURES / "SSD11-member-schedule.dxf"

pytestmark = pytest.mark.skipif(
    not DETAIL.exists() or not SCHEDULE.exists(), reason="CAD fixtures not present"
)


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


def _chunks(path: Path):
    async def build():
        raw = await DxfLoader().load(path)
        parsed = await DocumentParsingService(
            ocr_detector=OCRDetector(min_words_per_page=10.0),
            ocr_provider=_NoOCR(),
            labeled_analyzer=LabeledLayoutAnalyzer(),
            heuristic_analyzer=HeuristicLayoutAnalyzer(),
        ).process(raw, path, "dxf")
        pipeline = HybridChunkingPipeline(
            StructureChunker(),
            SemanticChunker(embedding_provider=_Embedder()),
            ParentChildChunker(ChunkingConfig()),
            ChunkValidator(),
        )
        return await pipeline.chunk(uuid.uuid4(), parsed)

    return asyncio.run(build())


def _passages(chunks, contains: str = "") -> list:
    """Chunks as the escalation gate sees them, optionally filtered."""
    out = []
    for chunk in chunks:
        if contains and contains.lower() not in chunk.content.lower():
            continue
        out.append(
            (
                str(chunk.document_id),
                "SSD09.0-02.dxf",
                chunk.content,
                chunk.chunk_metadata.page_number or 1,
                list(chunk.chunk_metadata.regions),
                list(chunk.chunk_metadata.layers),
            )
        )
    return out


@pytest.fixture(scope="module")
def detail_chunks():
    return _chunks(DETAIL)


@pytest.fixture(scope="module")
def schedule_chunks():
    return _chunks(SCHEDULE)


class TestDeterministicQuestionsNeverEscalate:
    """The first requirement: vision is not called when the file answers."""

    @pytest.mark.parametrize(
        "query",
        [
            "What is the bolt diameter?",
            "What bolt-related information is present in this drawing?",
            "What is the bent plate specification?",
            "What does the bent plate annotation refer to?",
            "What dimensions are associated with the bent plate?",
            "What is on the S-BOLTS layer?",
        ],
    )
    def test_no_escalation_for_the_phase_one_to_five_questions(self, detail_chunks, query):
        """Every question the earlier phases learned to answer stays
        deterministic. A vision call here would be a second opinion about a
        file that already said the answer."""
        decision = decide(query, _passages(detail_chunks))
        assert decision.escalate is False, decision.detail

    @pytest.mark.parametrize(
        "query",
        [
            "What is the quantity of BP-1?",
            "What material is BP-1?",
            "Which members use ASTM A36?",
        ],
    )
    def test_no_escalation_for_schedule_questions(self, schedule_chunks, query):
        assert decide(query, _passages(schedule_chunks)).escalate is False

    def test_a_question_with_no_evidence_does_not_escalate(self):
        """CASE E. The drawing carries no load capacity, and the correct
        answer is that it does not -- not a picture of somewhere it might be.

        Refused at the *first* gate rather than the empty-evidence one: a
        load capacity is a stated value, so the question was never visual to
        begin with. Two independent reasons to decline is the right number.
        """
        decision = decide("What is the load capacity?", [])
        assert decision.escalate is False
        assert decision.reason == EscalationReason.NOT_A_VISUAL_QUESTION

    def test_even_a_visual_question_declines_when_nothing_was_retrieved(self):
        """The second gate, reached only by a question that passed the first.
        Nothing retrieved means a bug or an absent fact, and a picture cannot
        tell those apart -- it can only produce something."""
        decision = decide("What symbol is shown in this detail?", [])
        assert decision.escalate is False
        assert decision.reason == EscalationReason.NO_EVIDENCE_AT_ALL


class TestVisualQuestionsOverUnresolvedRegions:
    def test_the_drawing_really_does_report_unresolved_annotations(self, detail_chunks):
        """The gate keys off this phrasing, so it is worth pinning that the
        loader still emits it."""
        text = "\n".join(c.content for c in detail_chunks)
        assert "could not be determined" in text

    def test_an_unlabelled_symbol_question_escalates_to_a_region(self, detail_chunks):
        """CASE C. `S-BOLTS` carries 13 block placements of 4 shapes that the
        block analysis deliberately refuses to name -- a DXF does not record
        what a symbol means. That is the legitimate vision case."""
        passages = _passages(detail_chunks, contains="could not be determined")
        if not any(p[4] for p in passages):
            pytest.skip("the unresolved chunk carried no region on this build")
        decision = decide("What does this unlabelled symbol represent?", passages)
        assert decision.escalate is True
        assert decision.candidates

    def test_escalation_never_selects_more_than_the_cap(self, detail_chunks):
        decision = decide("What symbol is shown here?", _passages(detail_chunks), max_regions=2)
        assert len(decision.candidates) <= 2

    def test_a_selected_region_is_a_crop_not_the_sheet(self, detail_chunks):
        """Never send the whole drawing."""
        passages = _passages(detail_chunks, contains="could not be determined")
        if not any(p[4] for p in passages):
            pytest.skip("the unresolved chunk carried no region on this build")
        decision = decide("What symbol is this?", passages)
        for candidate in decision.candidates:
            region = candidate.region
            assert region.space == "sheet"
            assert 0.0 <= region.x0 <= region.x1 <= 1.0
            assert region.area < 0.9, "a region covering the sheet is not a crop"


class TestSymbolRegionPropagation:
    """The region an unnamed symbol needs, from the loader to the gate.

    Regression for a bug that reached the live corpus: the symbol region was
    derived from block *insert points*, which are origins with no extent, so
    a symbol placed once produced a zero-area box that `Region.merge`
    correctly discarded. The chunk then carried no region, and the one
    genuinely visual question on the sheet had nowhere to look.
    """

    def test_unnamed_symbols_are_individually_locatable(self):
        raw = asyncio.run(DxfLoader().load(DETAIL))
        unnamed = [b for b in raw.text_blocks if b.text.startswith("An unnamed symbol")]
        assert unnamed, "no unnamed symbol was given a region"
        for block in unnamed:
            assert block.bbox is not None
            assert block.bbox.space == "sheet"

    def test_a_symbol_region_is_a_crop_not_the_drawing(self):
        """The forbidden fix. A region covering the sheet would make the
        vision fallback render the whole drawing, which is worse than
        declining to look at all."""
        raw = asyncio.run(DxfLoader().load(DETAIL))
        for block in raw.text_blocks:
            if not block.text.startswith("An unnamed symbol"):
                continue
            box = block.bbox
            assert box is not None
            area = (box.x1 - box.x0) * (box.y1 - box.y0)
            assert area < 0.25, f"region covers {area:.0%} of the sheet"

    def test_a_single_placement_still_yields_a_usable_region(self):
        """The exact failure. One insert point encloses to a point, whose
        area is zero -- so the region has to come from the geometry inside the
        block, not from where the block was placed."""
        raw = asyncio.run(DxfLoader().load(DETAIL))
        singles = [
            b
            for b in raw.text_blocks
            if b.text.startswith("An unnamed symbol") and "placed 1 time(s)" in b.text
        ]
        assert singles, "the drawing has singly-placed unnamed symbols to check"
        for block in singles:
            assert block.bbox is not None
            assert block.bbox.x1 > block.bbox.x0
            assert block.bbox.y1 > block.bbox.y0

    def test_the_sheet_summary_deliberately_carries_no_region(self):
        """It describes the whole drawing; cropping it would crop the
        drawing."""
        raw = asyncio.run(DxfLoader().load(DETAIL))
        summary = next(b for b in raw.text_blocks if b.text.startswith("Symbols placed on sheet"))
        assert summary.bbox is None

    def test_the_region_survives_chunking_and_the_search_payload(self, detail_chunks):
        """The propagation the trace covered: loader -> section -> segment ->
        ChunkMetadata -> payload -> back."""
        from src.infrastructure.serialization.chunk_payload import (
            chunk_to_payload,
            payload_to_chunk,
        )

        carriers = [
            c
            for c in detail_chunks
            if "unnamed symbol" in c.content.lower() and c.chunk_metadata.regions
        ]
        assert carriers, "no chunk reached the end of chunking with a symbol region"
        chunk = carriers[0]
        payload = chunk_to_payload(chunk)
        assert payload["regions"], "the region did not reach the search payload"
        assert payload_to_chunk(payload, chunk.id).chunk_metadata.regions

    def test_the_gate_escalates_with_a_specific_crop(self, detail_chunks):
        """End of the path: a visual question over these chunks escalates,
        and the candidate is a crop rather than the sheet."""
        passages = _passages(detail_chunks, contains="unnamed symbol")
        decision = decide("What does this unlabelled symbol represent?", passages)
        assert decision.escalate is True
        assert decision.candidates
        for candidate in decision.candidates:
            assert candidate.region.area < 0.25
