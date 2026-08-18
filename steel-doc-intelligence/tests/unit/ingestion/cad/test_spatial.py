"""Spatial association, tested against hand-built drawings.

No ezdxf and no fixture file here: every `CadDocument` is constructed in the
test, so a failure names a bug in the algorithm rather than a change in a CAD
library. The real drawing is exercised separately in `test_real_drawing.py`,
where the assertions are about *outcomes* rather than mechanics.

The coordinates are chosen to mirror the reference sheet's proportions --
1.5-unit text, callouts under a text-height from their leader, targets within
a couple of units of the tip -- so a tolerance that is wrong here is wrong
there too.
"""

from __future__ import annotations

import pytest

from src.ingestion.cad.models import (
    CadDocument,
    CadGeometry,
    CadTextEntity,
    LayoutSheet,
)
from src.ingestion.cad.spatial import (
    associate,
    build_chains,
    group_text_lines,
    parse_measurement,
)
from src.ingestion.loaders.base import BoundingBox

HEIGHT = 1.5


def text(
    body: str,
    x: float,
    y: float,
    layer: str = "S-TEXT",
    layout: int = 0,
    height: float = HEIGHT,
) -> CadTextEntity:
    """A text entity with the same bbox approximation the reader produces."""
    width = max(len(body), 1) * height * 0.6
    return CadTextEntity(
        text=body,
        layer=layer,
        layout_index=layout,
        bbox=BoundingBox(x0=x, y0=y, x1=x + width, y1=y + height, space="model"),
        height=height,
    )


def line(
    entity_id: str,
    points: list[tuple[float, float]],
    layer: str,
    layout: int = 0,
    kind: str = "LINE",
) -> CadGeometry:
    return CadGeometry(
        entity_id=entity_id,
        entity_type=kind,
        layer=layer,
        layout_index=layout,
        points=tuple(points),
    )


def drawing(
    texts: list[CadTextEntity],
    geometry: list[CadGeometry],
    layouts: int = 1,
) -> CadDocument:
    return CadDocument(
        layouts=[LayoutSheet(name=f"L{i}", index=i, is_model_space=i == 0) for i in range(layouts)],
        texts=texts,
        geometry=geometry,
    )


class TestLeaderDetection:
    """Leaders are plain lines on a leader-named layer, and have to be found."""

    def test_segments_on_a_leader_layer_are_treated_as_a_leader(self) -> None:
        cad = drawing(
            texts=[text("BENT PLATE 5x5", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(9.0, 20.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 1
        assert report.annotations[0].relation == "leader"

    def test_the_same_geometry_on_an_ordinary_layer_is_not_a_leader(self) -> None:
        """A line is a leader because of the layer it is on, not its shape.

        Without this the section outline of the member itself would be read as
        a leader and every callout would resolve to whatever the outline
        happened to end near.
        """
        cad = drawing(
            texts=[text("BENT PLATE 5x5", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-SECT_STEEL"),
                line("G1", [(9.0, 20.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 0
        assert report.annotations[0].relation == "unresolved"

    def test_arrowheads_are_not_chained_into_the_leader(self) -> None:
        """The bug that broke every callout on the real sheet.

        An arrowhead attaches at the leader's tip. Chain it in and the tip
        stops being a free end, so the only free end left is the one at the
        text -- and the leader appears to point at its own label.
        """
        cad = drawing(
            texts=[text("PURLIN, SEE PLAN", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("A1", [(10.0, 20.5), (10.6, 20.8)], "S-ARROW_HEAD"),
                line("A2", [(10.0, 20.5), (10.6, 20.2)], "S-ARROW_HEAD"),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 1
        assert report.annotations[0].target_layer == "S-SECT_STEEL"


class TestChainAssembly:
    """Separate segments become the polyline a reader perceives."""

    def test_touching_segments_join_into_one_chain(self) -> None:
        segments = [
            line("A", [(0.0, 0.0), (5.0, 0.0)], "S-LEADER"),
            line("B", [(5.0, 0.0), (5.0, 10.0)], "S-LEADER"),
            line("C", [(5.0, 10.0), (15.0, 10.0)], "S-LEADER"),
        ]
        chains = build_chains(segments)
        assert len(chains) == 1
        assert len(chains[0].segments) == 3

    def test_the_chain_free_ends_are_its_two_extremities(self) -> None:
        """The endpoints no other segment shares: where the text is and where
        the leader points."""
        segments = [
            line("A", [(0.0, 0.0), (5.0, 0.0)], "S-LEADER"),
            line("B", [(5.0, 0.0), (5.0, 10.0)], "S-LEADER"),
            line("C", [(5.0, 10.0), (15.0, 10.0)], "S-LEADER"),
        ]
        ends = set(build_chains(segments)[0].free_ends)
        assert ends == {(0.0, 0.0), (15.0, 10.0)}

    def test_separate_leaders_stay_separate(self) -> None:
        segments = [
            line("A", [(0.0, 0.0), (5.0, 0.0)], "S-LEADER"),
            line("B", [(40.0, 40.0), (45.0, 40.0)], "S-LEADER"),
        ]
        assert len(build_chains(segments)) == 2

    def test_a_shared_spine_yields_three_free_ends(self) -> None:
        """Two callouts on one spine is ordinary drafting, and is why chains
        are a graph rather than a path."""
        segments = [
            line("A", [(0.0, 0.0), (5.0, 0.0)], "S-LEADER"),
            line("B", [(5.0, 0.0), (10.0, 5.0)], "S-LEADER"),
            line("C", [(5.0, 0.0), (10.0, -5.0)], "S-LEADER"),
        ]
        chains = build_chains(segments)
        assert len(chains) == 1
        assert len(chains[0].free_ends) == 3


class TestTextToLeader:
    """Which end of the leader is the text end."""

    def test_the_text_end_is_chosen_by_the_text_box_not_the_insertion_point(self) -> None:
        """`PURLIN, SEE PLAN` is inserted at x=11.5 with its leader ending at
        x=28.9. Measured from the insertion point that is 16 units and looks
        like nothing; the text runs to x=25.9, so the real gap is about 3.
        """
        callout = text("PURLIN, SEE PLAN", 11.5, 37.0)
        assert callout.bbox is not None
        assert callout.bbox.x1 == pytest.approx(25.9, abs=0.5)

        cad = drawing(
            texts=[callout],
            geometry=[
                line("L1", [(28.9, 37.9), (43.2, 58.0)], "S-LEADER"),
                line("G1", [(43.0, 55.0), (43.0, 60.0)], "S-SECT_STEEL_THRU"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 1
        assert report.annotations[0].target_layer == "S-SECT_STEEL_THRU"

    def test_a_leader_far_from_every_text_claims_none_of_them(self) -> None:
        cad = drawing(
            texts=[text("BENT PLATE", 200.0, 200.0)],
            geometry=[
                line("L1", [(0.0, 0.0), (10.0, 0.0)], "S-LEADER"),
                line("G1", [(11.0, 0.0), (11.0, 5.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 0

    def test_one_annotation_per_text_even_when_several_leaders_reach_it(self) -> None:
        """Two chains converging on one callout must not assert it twice about
        two different pieces of geometry."""
        callout = text("3 SPACES", 20.0, 20.0)
        cad = drawing(
            texts=[callout],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("L2", [(19.5, 21.0), (12.0, 26.0)], "S-LEADER"),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
                line("G2", [(11.5, 26.0), (11.5, 30.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert len(report.annotations) == 1


class TestLeaderToGeometry:
    """What sits at the pointing end."""

    def test_the_target_is_at_the_far_end_not_nearest_the_text(self) -> None:
        """The whole reason this module exists.

        The callout sits beside a line it has nothing to do with, and 30 units
        away is the part its leader actually points at. Sorting by distance
        ranks the wrong one first.
        """
        cad = drawing(
            texts=[text("BENT PLATE 5x5x 12 GA.", 80.0, 92.0)],
            geometry=[
                line("DECOY", [(79.0, 90.0), (79.0, 96.0)], "S-SECT_STEEL"),
                line("A", [(79.5, 92.5), (52.0, 92.5)], "S-LEADER"),
                line("B", [(52.0, 92.5), (52.0, 74.0)], "S-LEADER"),
                line("C", [(52.0, 74.0), (48.8, 74.0)], "S-LEADER"),
                line("PLATE", [(48.0, 73.0), (48.0, 76.0)], "S-SECT_STEEL_THRU"),
            ],
        )
        report = associate(cad)
        assert report.resolved == 1
        found = report.annotations[0]
        assert found.target_entity_id == "PLATE"
        assert found.target_layer == "S-SECT_STEEL_THRU"

    def test_scaffolding_layers_are_never_the_target(self) -> None:
        """A leader tip lands beside dimension lines simply because dimensions
        are drawn in the clear space around a detail. Resolving to one would
        say a callout labels a measurement."""
        cad = drawing(
            texts=[text("PURLIN, SEE PLAN", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("D1", [(9.8, 20.5), (9.8, 24.0)], "S-DIMS"),
                line("G1", [(8.0, 18.0), (8.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        report = associate(cad)
        assert report.annotations[0].target_layer == "S-SECT_STEEL"

    def test_the_source_entity_id_is_carried_through(self) -> None:
        """An association that cannot be traced back to an entity cannot be
        checked, highlighted, or argued with."""
        cad = drawing(
            texts=[text("WF COLUMN", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("2F1A", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        found = associate(cad).annotations[0]
        assert found.target_entity_id == "2F1A"
        assert found.target_point is not None


class TestConfidenceAndRefusal:
    """The system prefers `unknown` to a confident wrong answer."""

    def test_a_leader_ending_in_empty_space_is_unresolved(self) -> None:
        cad = drawing(
            texts=[text("BENT PLATE", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(-400.0, -400.0), (-400.0, -395.0)], "S-SECT_STEEL"),
            ],
        )
        found = associate(cad).annotations[0]
        assert found.relation == "unresolved"
        assert found.confidence == 0.0
        assert "no geometry" in found.evidence

    def test_text_with_no_leader_at_all_is_unresolved(self) -> None:
        cad = drawing(
            texts=[text("BEAM TO COLUMN CONNECTION", 20.0, 20.0)],
            geometry=[line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL")],
        )
        found = associate(cad).annotations[0]
        assert found.relation == "unresolved"
        assert found.evidence == "no leader reaches this text"

    def test_a_tighter_leader_scores_higher(self) -> None:
        def score(gap: float) -> float:
            cad = drawing(
                texts=[text("BENT PLATE", 20.0, 20.0)],
                geometry=[
                    line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                    line("G1", [(10.0 - gap, 18.0), (10.0 - gap, 25.0)], "S-SECT_STEEL"),
                ],
            )
            return associate(cad).annotations[0].confidence

        assert score(0.2) > score(2.0) > score(4.0)

    def test_confidence_never_exceeds_one(self) -> None:
        cad = drawing(
            texts=[text("BENT PLATE", 20.0, 20.0)],
            geometry=[
                line("L1", [(20.0, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(10.0, 18.0), (10.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        assert associate(cad).annotations[0].confidence <= 1.0


class TestCoordinateSpace:
    """Model space and page space are never mixed, and neither are layouts."""

    def test_association_boxes_stay_in_model_space(self) -> None:
        cad = drawing(
            texts=[text("BENT PLATE", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        found = associate(cad).annotations[0]
        assert found.text_bbox is not None
        assert found.text_bbox.space == "model"

    def test_geometry_bboxes_are_model_space(self) -> None:
        geometry = line("G1", [(9.0, 18.0), (12.0, 25.0)], "S-SECT_STEEL")
        assert geometry.bbox is not None
        assert geometry.bbox.space == "model"
        assert (geometry.bbox.x0, geometry.bbox.y1) == (9.0, 25.0)

    def test_a_leader_never_reaches_across_layouts(self) -> None:
        """Two sheets share a coordinate range without sharing a space. A
        distance measured between them is meaningless, and acting on one
        would put a callout from sheet 1 onto a part on sheet 2."""
        cad = drawing(
            texts=[text("BENT PLATE", 20.0, 20.0, layout=0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER", layout=1),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL", layout=1),
            ],
            layouts=2,
        )
        report = associate(cad)
        assert report.resolved == 0

    def test_each_layout_is_resolved_in_its_own_frame(self) -> None:
        cad = drawing(
            texts=[
                text("PLATE A", 20.0, 20.0, layout=0),
                text("PLATE B", 20.0, 20.0, layout=1),
            ],
            geometry=[
                line("L0", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER", layout=0),
                line("G0", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL", layout=0),
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER", layout=1),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_CONC", layout=1),
            ],
            layouts=2,
        )
        by_layer = {a.text: a.target_layer for a in associate(cad).annotations}
        assert by_layer == {"PLATE A": "S-SECT_STEEL", "PLATE B": "S-SECT_CONC"}


class TestLayerPreservation:
    """Layer is the drawing's semantics and must survive to the annotation."""

    def test_both_layers_are_recorded(self) -> None:
        cad = drawing(
            texts=[text("WF COLUMN", 20.0, 20.0, layer="S-ANNO-TEXT")],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        found = associate(cad).annotations[0]
        assert found.text_layer == "S-ANNO-TEXT"
        assert found.target_layer == "S-SECT_STEEL"

    def test_leader_layers_are_matched_by_convention_not_exact_name(self) -> None:
        """`S-LEADER`, `LEADERS` and `A-ANNO-LEDR` all mean the same thing;
        layer naming is conventional rather than standardised."""
        for layer in ("S-LEADER", "LEADERS", "A-ANNO-LEDR", "leader"):
            cad = drawing(
                texts=[text("WF COLUMN", 20.0, 20.0)],
                geometry=[
                    line("L1", [(19.5, 20.5), (10.0, 20.5)], layer),
                    line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
                ],
            )
            assert associate(cad).resolved == 1, layer


class TestTextGrouping:
    """A callout drafted as stacked lines is one sentence."""

    def test_stacked_lines_on_the_same_layer_are_joined(self) -> None:
        grouped = group_text_lines(
            [
                text('ALL BOLTS 3/4" DIA. A325,', 80.0, 42.0),
                text("SEE BOLT SCHEDULE FOR", 80.0, 40.0),
                text("MINIMUM BOLT COUNT", 80.0, 38.0),
            ]
        )
        assert len(grouped) == 1
        assert grouped[0].text == (
            'ALL BOLTS 3/4" DIA. A325, SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT'
        )

    def test_the_merged_box_covers_the_whole_stack(self) -> None:
        grouped = group_text_lines([text("LINE ONE", 80.0, 42.0), text("LINE TWO", 80.0, 40.0)])
        box = grouped[0].bbox
        assert box is not None
        assert box.y0 == pytest.approx(40.0)
        assert box.y1 == pytest.approx(43.5)

    def test_texts_far_apart_are_not_joined(self) -> None:
        grouped = group_text_lines([text("T/SLAB", 10.0, 90.0), text("WF COLUMN", 10.0, 20.0)])
        assert len(grouped) == 2

    def test_texts_on_different_layers_are_not_joined(self) -> None:
        grouped = group_text_lines(
            [
                text("DRAWING TITLE", 10.0, 42.0, layer="TITLE"),
                text("WF COLUMN", 10.0, 40.0, layer="S-TEXT"),
            ]
        )
        assert len(grouped) == 2


class TestDrawnDimensions:
    """Numbers typed beside dimension geometry, reported as exactly that."""

    @pytest.mark.parametrize(
        ("body", "value", "unit"),
        [
            ('3"', 3.0, "in"),
            ('1 1/2"', 1.5, "in"),
            ("5'-6\"", 66.0, "in"),
            ("250mm", 250.0, "mm"),
            ("12.5", 12.5, ""),
            ('1/2" MAX.', 0.5, "in"),
        ],
    )
    def test_measurements_parse(self, body: str, value: float, unit: str) -> None:
        parsed = parse_measurement(body)
        assert parsed is not None
        assert parsed == (pytest.approx(value), unit)

    @pytest.mark.parametrize(
        "body",
        [
            "L3 1/2 x 3 1/2 x 5/16",
            "BENT PLATE 5x5x 12 GA.",
            "WF COLUMN",
            '3" PLATE',
            "",
            "4",
        ],
    )
    def test_non_measurements_are_rejected(self, body: str) -> None:
        """A bare integer is far more often a quantity or a detail number, and
        a specification that starts with a size is not a dimension."""
        assert parse_measurement(body) is None

    def test_a_measurement_beside_dimension_geometry_becomes_a_drawn_dimension(self) -> None:
        cad = drawing(
            texts=[text('3"', 61.5, 65.0)],
            geometry=[line("D1", [(62.5, 64.0), (62.5, 67.0)], "S-DIMS")],
        )
        report = associate(cad)
        assert len(report.drawn_dimensions) == 1
        record = report.drawn_dimensions[0]
        assert record.measurement == 3.0
        assert record.unit == "in"
        assert record.text_override == '3"'
        assert record.target_entity_id == "D1"

    def test_a_drawn_dimension_is_never_claimed_as_exact(self) -> None:
        """The file does not assert that the geometry measures 3 inches; a
        drafter typed it. `has_exact_value` is the only thing standing between
        that and a reported measurement."""
        cad = drawing(
            texts=[text('3"', 61.5, 65.0)],
            geometry=[line("D1", [(62.5, 64.0), (62.5, 67.0)], "S-DIMS")],
        )
        record = associate(cad).drawn_dimensions[0]
        assert record.source == "drawn"
        assert record.has_exact_value is False

    def test_a_measurement_with_no_dimension_geometry_nearby_is_not_a_dimension(self) -> None:
        cad = drawing(
            texts=[text('1/2" MAX. (TYP.)', 11.0, 41.0)],
            geometry=[line("D1", [(80.0, 80.0), (80.0, 85.0)], "S-DIMS")],
        )
        report = associate(cad)
        assert report.drawn_dimensions == []
        assert report.annotations[0].relation == "unresolved"


class TestScaleIndependence:
    """Tolerances follow the drawing's own text height, not its extents."""

    def test_the_same_detail_resolves_at_ten_times_the_scale(self) -> None:
        def resolve(k: float) -> int:
            cad = drawing(
                texts=[text("BENT PLATE", 20.0 * k, 20.0 * k, height=HEIGHT * k)],
                geometry=[
                    line("L1", [(19.5 * k, 20.5 * k), (10.0 * k, 20.5 * k)], "S-LEADER"),
                    line("G1", [(9.0 * k, 18.0 * k), (9.0 * k, 25.0 * k)], "S-SECT_STEEL"),
                ],
            )
            return associate(cad).resolved

        assert resolve(1.0) == resolve(10.0) == resolve(0.1) == 1

    def test_one_far_off_entity_does_not_move_the_tolerances(self) -> None:
        """The reference drawing has geometry at x=1529 against a detail 100
        wide. Deriving tolerances from extents would make everything on the
        sheet 'nearby'."""
        cad = drawing(
            texts=[text("BENT PLATE", 20.0, 20.0)],
            geometry=[
                line("L1", [(19.5, 20.5), (10.0, 20.5)], "S-LEADER"),
                line("G1", [(9.0, 18.0), (9.0, 25.0)], "S-SECT_STEEL"),
                line("FAR", [(1529.0, 0.0), (1530.0, 1.0)], "S-SECT_STEEL"),
                line("DECOY", [(40.0, 20.0), (40.0, 25.0)], "S-SECT_STEEL"),
            ],
        )
        assert associate(cad).annotations[0].target_entity_id == "G1"


class TestPerformance:
    """Candidate pruning, not an all-pairs sweep."""

    def test_a_large_drawing_resolves_without_an_all_pairs_sweep(self) -> None:
        """4,000 primitives against 200 callouts is 800,000 pairs unpruned.
        The grid keeps each lookup proportional to what is actually nearby, so
        this finishes in well under a second rather than tens of seconds.
        """
        texts = [text(f"CALLOUT {i}", 20.0 + i * 40, 20.0) for i in range(200)]
        geometry: list[CadGeometry] = []
        for i in range(200):
            base = 20.0 + i * 40
            geometry.append(line(f"L{i}", [(base - 0.5, 20.5), (base - 10, 20.5)], "S-LEADER"))
            geometry.append(line(f"G{i}", [(base - 11, 18.0), (base - 11, 25.0)], "S-SECT_STEEL"))
        for i in range(3600):
            x = float(i % 200) * 40
            geometry.append(line(f"N{i}", [(x, 400.0 + i), (x + 1, 401.0 + i)], "S-SECT_STEEL"))

        report = associate(drawing(texts=texts, geometry=geometry))
        assert report.resolved == 200
