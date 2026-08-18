"""Block semantics, tested against hand-built drawings.

No ezdxf and no fixture file: every `CadDocument` is built in the test, so a
failure names a bug in the grouping rather than a change in a CAD library.

The thing under test is a claim about a drawing, so most of these are about
the claims it must *not* make. Over-grouping says two different symbols are
the same; under-grouping says thirteen definitions are thirteen different
things when they are four; and naming a shape says what it represents, which
the file does not state.
"""

from __future__ import annotations

import pytest

from src.ingestion.cad.blocks import describe_blocks, signature_of
from src.ingestion.cad.models import (
    BlockDefinition,
    CadDocument,
    LayoutSheet,
    PartInstance,
)


def definition(
    name: str,
    counts: dict[str, int],
    texts: tuple[str, ...] = (),
    width: float = 1.0,
    height: float = 1.0,
) -> BlockDefinition:
    return BlockDefinition(
        name=name,
        is_anonymous=name.startswith("*"),
        primitive_counts=counts,
        texts=texts,
        width=width,
        height=height,
    )


def placement(name: str, layer: str, depth: int = 0, layout: int = 0) -> PartInstance:
    return PartInstance(
        block_name=name,
        layer=layer,
        layout_index=layout,
        insert_point=(0.0, 0.0),
        depth=depth,
    )


def drawing(
    definitions: list[BlockDefinition],
    parts: list[PartInstance],
    layouts: int = 1,
) -> CadDocument:
    return CadDocument(
        layouts=[LayoutSheet(name=f"L{i}", index=i) for i in range(layouts)],
        block_definitions=definitions,
        parts=parts,
    )


class TestNamingByContent:
    """A block's own text is the best label available and costs nothing."""

    def test_an_anonymous_block_is_named_by_its_text(self):
        block = definition(
            "*U8",
            {"TEXT": 3},
            texts=('ALL BOLTS 3/4" DIA. A325,', "SEE BOLT SCHEDULE FOR", "MINIMUM BOLT COUNT"),
        )
        assert block.label == 'ALL BOLTS 3/4" DIA. A325, SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT'

    def test_a_named_block_keeps_its_name(self):
        """A drafter who called it `AXARROW` has already said what it is."""
        assert definition("AXARROW", {"LINE": 3}).label == "AXARROW"

    def test_a_geometry_only_block_gets_no_invented_label(self):
        assert definition("*U25", {"LINE": 16, "SOLID": 16}).label == ""

    def test_a_named_block_in_a_group_supplies_the_label(self):
        cad = drawing(
            [
                definition("SYM", {"LINE": 4}),
                definition("*U1", {"LINE": 4}),
            ],
            [placement("SYM", "S-MISC"), placement("*U1", "S-MISC")],
        )
        group = describe_blocks(cad).groups[0]
        assert group.label == "SYM"
        assert set(group.definitions) == {"SYM", "*U1"}


class TestShapeGrouping:
    """Thirteen definitions that are four shapes."""

    def test_identical_definitions_group(self):
        cad = drawing(
            [
                definition(f"*U{i}", {"LINE": 16, "SOLID": 16}, width=80.8, height=0.8)
                for i in range(5)
            ],
            [placement(f"*U{i}", "S-BOLTS") for i in range(5)],
        )
        report = describe_blocks(cad)
        assert report.distinct_shapes == 1
        assert report.groups[0].placements == 5
        assert len(report.groups[0].definitions) == 5

    def test_same_primitives_at_a_different_size_are_a_different_shape(self):
        """`*U4` and `*U35` are both four lines and two solids. One is an
        arrowhead a fifth of a unit wide; the other is a leader forty-five
        units long. Primitive counts alone would call them one symbol."""
        cad = drawing(
            [
                definition("*U4", {"LINE": 4, "SOLID": 2}, width=0.188, height=0.03),
                definition("*U35", {"LINE": 4, "SOLID": 2}, width=45.5, height=1.5),
            ],
            [placement("*U4", "S-ARROW_HEAD"), placement("*U35", "S-LEADER")],
        )
        assert describe_blocks(cad).distinct_shapes == 2

    def test_different_text_is_a_different_shape(self):
        """Nine unrelated callouts are each one TEXT at the same size. Without
        the text in the signature they collapse into "one symbol used nine
        times", which is a plain falsehood about the drawing."""
        cad = drawing(
            [
                definition("*U7", {"TEXT": 1}, texts=("BEAM, SEE PLAN",), width=0, height=0),
                definition("*U19", {"TEXT": 1}, texts=("PURLIN, SEE PLAN",), width=0, height=0),
                definition("*U10", {"TEXT": 1}, texts=('3"',), width=0, height=0),
                definition("*U11", {"TEXT": 1}, texts=('3"',), width=0, height=0),
            ],
            [placement(n, "S-TEXT") for n in ("*U7", "*U19", "*U10", "*U11")],
        )
        report = describe_blocks(cad)
        assert report.distinct_shapes == 3
        threes = next(g for g in report.groups if g.label == '3"')
        assert threes.placements == 2

    def test_the_signature_tolerates_floating_point_noise(self):
        """Exact rounded coordinates were tried first and fragmented: three
        byte-identical bolt blocks landed in three buckets because one vertex
        sat on a rounding boundary."""
        a = definition("*A", {"LINE": 16}, width=80.79312, height=0.75)
        b = definition("*B", {"LINE": 16}, width=80.79308, height=0.7500001)
        assert signature_of(a) == signature_of(b)

    def test_a_genuinely_different_size_still_separates(self):
        a = definition("*A", {"LINE": 16, "SOLID": 16}, width=80.8, height=0.8)
        b = definition("*B", {"LINE": 16, "SOLID": 16}, width=96.7, height=0.8)
        assert signature_of(a) != signature_of(b)


class TestCountingPlacements:
    """What is on the sheet, not what the traversal walked past."""

    def test_nested_placements_are_counted_separately(self):
        """19 arrowheads each containing one nested block are 19 symbols and
        38 INSERTs. A schedule reporting 38 is counting the drawing's
        internals rather than its contents."""
        cad = drawing(
            [
                definition("AXARROW", {"LINE": 3, "INSERT": 1}),
                definition("*U4", {"LINE": 4, "SOLID": 2}, width=0.2, height=0.03),
            ],
            [placement("AXARROW", "S-ARROW_HEAD") for _ in range(19)]
            + [placement("*U4", "S-ARROW_HEAD", depth=1) for _ in range(19)],
        )
        report = describe_blocks(cad)
        arrow = next(g for g in report.groups if g.label == "AXARROW")
        inner = next(g for g in report.groups if not g.label)
        assert arrow.placements == 19
        assert inner.placements == 0
        assert inner.nested_placements == 19
        assert report.per_layer["S-ARROW_HEAD"] == (19, 1)

    def test_parts_summary_can_exclude_nested_placements(self):
        cad = drawing(
            [definition("AXARROW", {"LINE": 3})],
            [placement("AXARROW", "S-ARROW_HEAD"), placement("*U4", "S-ARROW_HEAD", depth=1)],
        )
        assert sum(cad.parts_summary().values()) == 2
        assert sum(cad.parts_summary(top_level_only=True).values()) == 1

    def test_definitions_never_placed_are_left_out(self):
        """A template declares far more blocks than a sheet uses, and listing
        those describes the template rather than the drawing."""
        cad = drawing(
            [definition("USED", {"LINE": 1}), definition("UNUSED", {"LINE": 2})],
            [placement("USED", "S-MISC")],
        )
        report = describe_blocks(cad)
        assert [g.label for g in report.groups] == ["USED"]

    def test_placements_are_counted_per_layout(self):
        cad = drawing(
            [definition("SYM", {"LINE": 1})],
            [
                placement("SYM", "S-MISC", layout=0),
                placement("SYM", "S-MISC", layout=1),
                placement("SYM", "S-MISC", layout=1),
            ],
            layouts=2,
        )
        assert describe_blocks(cad, layout_index=0).groups[0].placements == 1
        assert describe_blocks(cad, layout_index=1).groups[0].placements == 2
        assert describe_blocks(cad).groups[0].placements == 3

    def test_an_empty_drawing_reports_nothing(self):
        report = describe_blocks(drawing([], []))
        assert report.groups == []
        assert report.per_layer == {}


class TestRestraint:
    """The claims this must not make."""

    def test_a_geometry_shape_is_never_described_as_a_part(self):
        """13 placements on a layer called S-BOLTS is not 13 bolts, and this
        drawing says so itself: `SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT`.
        The count is deliberately not on the sheet."""
        cad = drawing(
            [
                definition(f"*U{i}", {"LINE": 16, "SOLID": 16}, width=80.8, height=0.8)
                for i in range(13)
            ],
            [placement(f"*U{i}", "S-BOLTS") for i in range(13)],
        )
        group = describe_blocks(cad).groups[0]
        assert group.label == ""
        assert "bolt" not in group.summary().lower()
        assert "unnamed shape" in group.summary()

    def test_the_summary_of_a_labelled_symbol_is_its_own_words(self):
        cad = drawing(
            [definition("*U8", {"TEXT": 1}, texts=('ALL BOLTS 3/4" DIA. A325',))],
            [placement("*U8", "S-TEXT")],
        )
        assert describe_blocks(cad).groups[0].summary() == '"ALL BOLTS 3/4" DIA. A325"'

    def test_per_layer_counts_shapes_not_kinds_of_part(self):
        cad = drawing(
            [
                definition("*A", {"LINE": 16, "SOLID": 16}, width=80.8, height=0.8),
                definition("*B", {"LINE": 16, "SOLID": 16}, width=80.8, height=0.8),
                definition("*C", {"LINE": 30, "SOLID": 32}, width=88.9, height=0.8),
            ],
            [placement(n, "S-BOLTS") for n in ("*A", "*B", "*C")],
        )
        placements, shapes = describe_blocks(cad).per_layer["S-BOLTS"]
        assert (placements, shapes) == (3, 2)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("BEAM, SEE PLAN", "BEAM, SEE PLAN"),
        ("x" * 70, "x" * 61 + "..."),
    ],
)
def test_long_labels_are_shortened_for_a_table_cell(label, expected):
    """One of this drawing's blocks is a 350-character legal disclaimer, and
    whole in a cell it swamps the schedule."""
    from src.ingestion.loaders.dxf_loader import _shorten

    assert _shorten(label) == expected
