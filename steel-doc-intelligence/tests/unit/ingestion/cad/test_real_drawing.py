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

from src.ingestion.cad.blocks import describe_blocks
from src.ingestion.cad.dxf_reader import DxfReader
from src.ingestion.cad.spatial import associate
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
def report(cad):
    return associate(cad)


@pytest.fixture(scope="module")
def blocks(cad):
    return describe_blocks(cad)


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


class TestSpatialAssociation:
    """What each callout points at, on the drawing rather than in a fixture.

    Ground truth here is the sheet itself, read by eye: seven leader-line
    callouts, a stack of bolt-spacing dimensions on the right of the detail,
    and a handful of texts -- the disclaimer, the scale, the detail title --
    that point at nothing and never did.
    """

    def test_geometry_is_captured_with_coordinates(self, cad):
        """It used to be counted and thrown away, which is why nothing could
        be associated with anything."""
        assert len(cad.geometry) > 500
        assert all(g.points for g in cad.geometry)

    def test_leaders_are_reassembled_into_chains(self, report):
        """26 leader segments on `S-LEADER`, and zero native LEADER entities.
        Joined by their shared endpoints they are 9 leaders, which is what a
        person sees on the sheet."""
        assert 8 <= len(report.chains) <= 12
        assert any(len(c.segments) >= 3 for c in report.chains)

    def test_every_leader_callout_resolves(self, report):
        assert report.resolved == 7

    def test_the_bent_plate_annotation_resolves_to_the_plate(self, report):
        """The question the whole phase exists for.

        The nearest drawn line to this callout is 18 units away on
        `S-SECT_STEEL` -- part of the section outline at the sheet's right
        edge. The plate is 36 units away, and the drawing says so with a
        three-segment leader. Nearest-entity ranks the wrong one first.
        """
        found = next(a for a in report.annotations if a.text.startswith("BENT PLATE"))
        assert found.relation == "leader"
        assert found.target_layer == "S-SECT_STEEL_THRU"
        assert found.confidence > 0.8
        assert found.target_entity_id

    def test_callouts_split_across_lines_are_rejoined(self, report):
        """`ALL BOLTS 3/4" DIA. A325,` / `SEE BOLT SCHEDULE FOR` / `MINIMUM
        BOLT COUNT` are three TEXT entities and one sentence. Only the first
        carries the leader, so ungrouped, two thirds of the bolt spec is
        stranded and the resolved third ends in a comma."""
        found = next(a for a in report.annotations if a.text.startswith("ALL BOLTS"))
        assert "MINIMUM BOLT COUNT" in found.text
        assert found.relation == "leader"

    def test_the_concrete_callout_lands_on_the_concrete(self, report):
        found = next(a for a in report.annotations if a.text.startswith("TYP. CONCRETE"))
        assert found.target_layer == "S-SECT_CONC"

    def test_the_column_and_beam_callouts_land_on_steel(self, report):
        by_start = {a.text.split(",")[0]: a for a in report.annotations}
        assert by_start["WF COLUMN"].target_layer.startswith("S-SECT_STEEL")
        assert by_start["BEAM"].target_layer.startswith("S-SECT_STEEL")

    def test_sheet_furniture_stays_unresolved(self, report):
        """The disclaimer, the scale note and the detail title point at
        nothing. Claiming otherwise would be the failure mode this design is
        built to avoid."""
        unresolved = {a.text for a in report.annotations if a.relation == "unresolved"}
        assert any("SCALE" in t for t in unresolved)
        assert any("BEAM TO COLUMN CONNECTION" in t for t in unresolved)

    def test_no_association_is_asserted_at_low_confidence(self, report):
        resolved = [a for a in report.annotations if a.relation != "unresolved"]
        assert resolved
        assert all(a.confidence >= 0.5 for a in resolved)

    def test_every_resolved_annotation_cites_a_source_entity(self, report):
        for found in report.annotations:
            if found.relation == "unresolved":
                continue
            assert found.target_entity_id, found.text
            assert found.target_point is not None
            assert found.evidence

    def test_drawn_dimensions_are_found(self, report):
        """Five `3"` bolt spacings and a `1 1/2"` edge distance, drawn as text
        beside tick marks because the file has no DIMENSION entities."""
        values = sorted(d.measurement for d in report.drawn_dimensions)
        assert values == [1.5, 3.0, 3.0, 3.0, 3.0, 3.0]
        assert all(d.unit == "in" for d in report.drawn_dimensions)

    def test_drawn_dimensions_are_never_claimed_as_measured(self, report):
        assert all(not d.has_exact_value for d in report.drawn_dimensions)
        assert all(d.source == "drawn" for d in report.drawn_dimensions)

    def test_coordinates_stay_in_model_space(self, report):
        for found in report.annotations:
            if found.text_bbox is not None:
                assert found.text_bbox.space == "model"
        for record in report.drawn_dimensions:
            assert record.bbox is not None
            assert record.bbox.space == "model"

    def test_layers_survive_onto_the_annotation(self, report):
        for found in report.annotations:
            assert found.text_layer
            if found.relation != "unresolved":
                assert found.target_layer


class TestBlockSemantics:
    """The block schedule, on the drawing rather than in a fixture.

    Ground truth by inspection: 36 block definitions reachable from the sheet,
    of which most are R12 anonymisation artefacts. Thirteen sit on `S-BOLTS`
    and are four repeated shapes; nineteen arrowheads each wrap one nested
    block; and every callout lives in a block whose text names it.
    """

    def test_block_definitions_are_recorded(self, cad):
        assert len(cad.block_definitions) == 36
        assert all(d.name for d in cad.block_definitions)

    def test_anonymous_text_blocks_name_themselves(self, blocks):
        """`*U8` is the bolt note, and saying `*U8` in a schedule says
        nothing."""
        labels = {g.label for g in blocks.groups}
        assert 'ALL BOLTS 3/4" DIA. A325, SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT' in labels
        assert "BENT PLATE 5x5x 12 GA. (4) SIDES OF COLUMN FOR DECK BEARING (TYP.)" in labels
        assert "WF COLUMN, SEE PLAN AND SCHEDULE" in labels

    def test_named_blocks_keep_their_names(self, blocks):
        labels = {g.label for g in blocks.groups}
        assert "AXARROW" in labels
        assert "TARGET" in labels

    def test_thirty_six_definitions_collapse_to_twenty_three_shapes(self, blocks):
        assert blocks.definition_count == 36
        assert blocks.distinct_shapes == 23

    def test_the_bolts_layer_is_four_shapes_not_thirteen_things(self, blocks):
        """The whole point of the phase: `*U22 x1, *U23 x1, *U24 x1 …` was a
        list; `13 placements of 4 shapes` is the beginning of a count."""
        assert blocks.per_layer["S-BOLTS"] == (13, 4)

    def test_the_five_identical_dimension_texts_group(self, blocks):
        threes = next(g for g in blocks.groups if g.label == '3"')
        assert threes.placements == 5
        assert len(threes.definitions) == 5

    def test_arrowheads_are_counted_once_not_twice(self, blocks):
        """19 arrowheads each contain one nested block. Counting every INSERT
        the traversal walks past reports 38."""
        assert blocks.per_layer["S-ARROW_HEAD"] == (19, 1)
        inner = [g for g in blocks.groups if g.nested_placements == 19]
        assert inner and inner[0].placements == 0

    def test_no_geometry_shape_is_given_an_invented_name(self, blocks):
        for group in blocks.groups:
            if group.describes_itself:
                continue
            assert "unnamed shape" in group.summary()

    def test_unplaced_template_blocks_are_excluded(self, cad, blocks):
        """The block table holds definitions nothing on the sheet references;
        listing those describes the template, not the drawing."""
        placed = {p.block_name for p in cad.parts}
        for group in blocks.groups:
            assert set(group.definitions) & placed


class TestSymbolChunks:
    """The counts have to reach retrieval, and carry their caveat with them."""

    def test_a_symbol_schedule_replaces_the_block_schedule(self, raw):
        captions = [t.caption or "" for t in raw.tables]
        assert any("Symbol schedule" in c for c in captions)
        assert not any("Block schedule" in c for c in captions)

    def test_the_schedule_names_symbols_rather_than_block_ids(self, raw):
        schedule = next(t for t in raw.tables if "Symbol schedule" in (t.caption or ""))
        assert "AXARROW" in schedule.markdown
        assert "*U10" not in schedule.markdown
        assert "*U22" not in schedule.markdown

    def test_the_schedule_omits_an_empty_marks_column(self, raw):
        """This drawing has no piece-mark attributes at all, and an empty
        column reads as "no marks recorded" rather than "none exist"."""
        schedule = next(t for t in raw.tables if "Symbol schedule" in (t.caption or ""))
        assert "Marks" not in schedule.markdown.splitlines()[0]

    def test_the_summary_chunk_states_the_per_layer_counts(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "S-BOLTS: 13 BLOCK PLACEMENT(S) OF 4 DISTINCT SHAPE(S)" in text

    def test_the_summary_refuses_to_call_a_placement_a_part(self, chunks):
        """Without this sentence a reader concludes 13 bolts, which is the one
        number the drawing deliberately withholds."""
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "A BLOCK PLACEMENT IS NOT A PART COUNT" in text

    def test_no_chunk_claims_a_bolt_count(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        for invented in ("13 BOLTS", "4 BOLTS", "13 BOLT ", "26 BOLTS"):
            assert invented not in text, invented


class TestRegions:
    """Citations resolve to a rectangle on the sheet, not to the whole sheet.

    The drawing's own declared extents are (0,0)-(116,116) and its content
    runs out to x=-114, so the frame is the union of the two. Coordinates
    below are normalised against that.
    """

    def test_the_reader_records_the_drawings_declared_extents(self, cad):
        model = next(s for s in cad.layouts if s.is_model_space)
        assert model.extents is not None
        assert model.extents[2] == pytest.approx(116.0)
        assert model.extents[3] == pytest.approx(116.0)

    def test_annotation_blocks_carry_a_region(self, raw):
        """Seven resolved callouts, plus the block listing the unresolved
        ones. That last region is the one a vision fallback crops: an
        annotation the extraction could not resolve is the only case where
        looking at the drawing might add something."""
        annotated = [b for b in raw.text_blocks if b.element_label == "cad_annotation"]
        located = [b for b in annotated if b.bbox is not None]
        assert len(located) == 8
        assert sum(1 for b in located if "could not be determined" in b.text) == 1

    def test_regions_are_normalised_sheet_space(self, raw):
        for block in raw.text_blocks:
            if block.bbox is None:
                continue
            assert block.bbox.space == "sheet", block.text[:40]
            assert 0.0 <= block.bbox.x0 <= block.bbox.x1 <= 1.0
            assert 0.0 <= block.bbox.y0 <= block.bbox.y1 <= 1.0

    def test_the_bent_plate_note_lands_where_the_note_is(self, raw):
        """It sits at model (81, 87..94) on a 116-tall sheet, so near the top
        right. Y-down means a small y -- getting the flip wrong would put it
        at 0.75 and look entirely plausible."""
        block = next(
            b
            for b in raw.text_blocks
            if b.element_label == "cad_annotation" and "BENT PLATE" in b.text
        )
        assert block.bbox is not None
        assert block.bbox.x0 > 0.5, "the note is on the right of the sheet"
        assert block.bbox.y1 < 0.4, "and near the top"

    def test_a_callouts_region_is_tighter_than_a_layers(self, raw):
        """The point of per-annotation regions: a citation for the bent-plate
        note lands on the note rather than on everything S-TEXT says."""
        note = next(
            b
            for b in raw.text_blocks
            if b.element_label == "cad_annotation" and "BENT PLATE" in b.text
        )
        layer = next(b for b in raw.text_blocks if b.text.startswith("Layer S-TEXT:"))
        assert note.bbox is not None and layer.bbox is not None
        note_area = (note.bbox.x1 - note.bbox.x0) * (note.bbox.y1 - note.bbox.y0)
        layer_area = (layer.bbox.x1 - layer.bbox.x0) * (layer.bbox.y1 - layer.bbox.y0)
        assert note_area < layer_area / 10

    def test_chunks_carry_their_regions_through(self, chunks):
        _, chunk_list = chunks
        located = [c for c in chunk_list if c.chunk_metadata.regions]
        assert located, "no chunk reached the end of the pipeline with a region"
        for chunk in located:
            assert chunk.chunk_metadata.region_precision
            for region in chunk.chunk_metadata.regions:
                assert region.space == "sheet"
                assert 0.0 <= region.x0 <= region.x1 <= 1.0

    def test_a_regions_page_matches_its_chunk(self, chunks):
        """A rectangle attributed to the wrong sheet is worse than none."""
        _, chunk_list = chunks
        for chunk in chunk_list:
            for region in chunk.chunk_metadata.regions:
                assert region.page_number == chunk.chunk_metadata.page_number


class TestAssociationChunks:
    """The associations have to reach retrieval, or they answer nothing."""

    def test_an_annotation_chunk_exists(self, chunks):
        _, chunk_list = chunks
        assert any("LABELS A" in c.content.upper() for c in chunk_list)

    def test_the_bent_plate_target_is_retrievable(self, chunks):
        """ "What does the bent plate annotation refer to?" needs the answer
        written down somewhere a retriever can find it."""
        _, chunk_list = chunks
        hits = [
            c
            for c in chunk_list
            if "BENT PLATE" in c.content.upper() and "S-SECT_STEEL_THRU" in c.content.upper()
        ]
        assert hits

    def test_unresolved_annotations_say_so_in_words(self, chunks):
        _, chunk_list = chunks
        text = _all_text(chunk_list)
        assert "COULD NOT BE DETERMINED" in text

    def test_the_dimension_schedule_marks_drawn_values_as_drawn(self, raw):
        schedule = next(t for t in raw.tables if "Dimension schedule" in (t.caption or ""))
        assert "drawn text beside dimension geometry" in schedule.markdown
        assert "measured by CAD" not in schedule.markdown

    def test_annotation_chunks_carry_a_page_number(self, chunks):
        _, chunk_list = chunks
        for chunk in chunk_list:
            if "LABELS A" in chunk.content.upper():
                assert chunk.chunk_metadata.page_number is not None
