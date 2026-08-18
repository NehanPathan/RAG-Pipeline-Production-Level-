"""Generate the drawn-schedule test fixture.

    python scripts/make_schedule_fixture.py tests/fixtures/cad/SSD11-member-schedule.dxf

**Why this is generated rather than downloaded.** The natural fixture is
AxiomCPL's `SSD11.0` "Notes & Schedules" sample set -- the same publisher and
the same template system as `SSD09.0-02.dxf`, which is already committed here.
Those files were obtained and inspected during this work and are *not*
committed, for two independent reasons:

1. **Licensing.** axiomcpl.com states that redistribution requires written
   consent from Axiom Solutions, LLC. We do not have it.
2. **Format.** They ship as AutoCAD 2000 DWG. Reading them needs the ODA File
   Converter, which this project deliberately leaves for the operator to
   install under their own EULA acceptance (see `dwg_converter.py`).

So the structure below is reproduced rather than copied. The drafting
conventions are not invented: they are the ones observed in the real
`SSD09.0-02.dxf` across the earlier phases of this work -- R12/AC1009, text
living inside anonymous blocks rather than in model space, an `S-` layer
taxonomy, plain `LINE` entities for the grid, and `TEXT` at a uniform height.
A detector that works here is exercising the same code path a real sheet
takes, including the block traversal.

**The fixture is deliberately hostile.** Alongside the schedule it contains
every false positive that blocked this phase on the real drawing: evenly
spaced bolt gauge lines on `S-CENTERLINE`, a member outline on
`S-SECT_STEEL`, leader lines, arrowheads and a `REVIEW_TEXT_BOX` rectangle.
A detector that finds two tables here is as wrong as one that finds none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf

# The schedule, as an engineer would draft it. Column widths differ because
# real ones do -- a description column is wide and a quantity column is
# narrow, and a detector that assumes uniform columns fails on every real
# sheet.
TITLE = "MEMBER SCHEDULE"
HEADERS = ("MARK", "DESCRIPTION", "DWG.NO", "QTY", "WT. (KG)", "REMARKS")
ROWS = (
    ("CE-4", "ASTM A36 W12X40", "S-201", "1", "245", "COLUMN"),
    ("BP-1", "ASTM A36 25MM THK MS PLATE", "S-202", "1", "38", "GROUT 25MM"),
    ("AB-2", "ASTM A36 ANGLE BAR L75X75X6", "S-203", "4", "12", ""),
    ("HB-1", "ASTM A325 BOLTS 20MM DIA", "S-204", "16", "2", "HSFG"),
)

COLUMN_WIDTHS = (14.0, 46.0, 14.0, 8.0, 14.0, 20.0)
ROW_HEIGHT = 6.0
ORIGIN = (20.0, 40.0)
TEXT_HEIGHT = 1.8

LAYER_GRID = "S-SCHEDULE"
LAYER_TEXT = "S-TEXT"
LAYER_TITLE = "S-TITLE_TEXT"


def _column_edges() -> list[float]:
    edges = [ORIGIN[0]]
    for width in COLUMN_WIDTHS:
        edges.append(edges[-1] + width)
    return edges


def _row_edges() -> list[float]:
    # Drawn top-down as a reader sees it, so y descends; the topmost edge is
    # the table's top.
    top = ORIGIN[1] + (len(ROWS) + 1) * ROW_HEIGHT
    return [top - i * ROW_HEIGHT for i in range(len(ROWS) + 2)]


def _add_schedule(doc: ezdxf.document.Drawing) -> None:
    """The grid on its own layer, the text inside a block.

    Text goes in a block because that is what the real drawing does -- 35 of
    its 45 text entities live inside anonymous block definitions -- and it is
    what makes this fixture exercise the INSERT -> block -> transform path
    rather than quietly bypassing it.
    """
    msp = doc.modelspace()
    xs = _column_edges()
    ys = _row_edges()

    for y in ys:
        msp.add_line((xs[0], y), (xs[-1], y), dxfattribs={"layer": LAYER_GRID})
    for x in xs:
        msp.add_line((x, ys[-1]), (x, ys[0]), dxfattribs={"layer": LAYER_GRID})

    block = doc.blocks.new(name="SCHEDULE_TEXT")
    pad = 1.0
    grid = [HEADERS, *ROWS]
    for row_index, row in enumerate(grid):
        # ys[0] is the top edge; row 0 is the header band beneath it.
        baseline = ys[row_index + 1] + (ROW_HEIGHT - TEXT_HEIGHT) / 2
        for column_index, value in enumerate(row):
            if not value:
                continue
            block.add_text(
                value,
                dxfattribs={
                    "layer": LAYER_TEXT,
                    "height": TEXT_HEIGHT,
                    "insert": (xs[column_index] + pad, baseline),
                },
            )

    block.add_text(
        TITLE,
        dxfattribs={
            "layer": LAYER_TITLE,
            "height": TEXT_HEIGHT * 1.6,
            "insert": (xs[0], ys[0] + 2.0),
        },
    )
    msp.add_blockref("SCHEDULE_TEXT", (0, 0), dxfattribs={"layer": LAYER_TEXT})


def _add_false_positives(doc: ezdxf.document.Drawing) -> None:
    """Every grid-shaped thing on the real sheet that is not a table.

    These are not padding. Each one defeated the naive
    "3 horizontals + 3 verticals" detector on `SSD09.0-02.dxf`, and
    `S-CENTERLINE` is the worst of them: five bolt gauge lines at exactly 3.0
    spacing, crossed by four more, which is indistinguishable from table rows
    by geometry alone. What separates them is that no text sits in the cells.
    """
    msp = doc.modelspace()

    # Bolt gauge: evenly spaced, crossed, and empty.
    for i in range(5):
        y = 120.0 + i * 3.0
        msp.add_line((40.0, y), (56.0, y), dxfattribs={"layer": "S-CENTERLINE"})
    for x in (40.0, 44.4, 51.6, 56.0):
        msp.add_line((x, 120.0), (x, 132.0), dxfattribs={"layer": "S-CENTERLINE"})

    # Member outline: many axis-aligned lines, irregular spacing.
    outline = [
        ((80.0, 120.0), (120.0, 120.0)),
        ((80.0, 126.0), (120.0, 126.0)),
        ((80.0, 141.0), (120.0, 141.0)),
        ((80.0, 120.0), (80.0, 141.0)),
        ((98.0, 126.0), (98.0, 141.0)),
        ((102.0, 126.0), (102.0, 141.0)),
        ((120.0, 120.0), (120.0, 141.0)),
    ]
    for start, end in outline:
        msp.add_line(start, end, dxfattribs={"layer": "S-SECT_STEEL"})

    # A leader with an arrowhead, pointing at the outline.
    msp.add_line((130.0, 150.0), (124.0, 150.0), dxfattribs={"layer": "S-LEADER"})
    msp.add_line((124.0, 150.0), (124.0, 138.0), dxfattribs={"layer": "S-LEADER"})
    msp.add_line((124.0, 138.0), (121.0, 138.0), dxfattribs={"layer": "S-LEADER"})
    for dy in (-0.4, 0.4):
        msp.add_line((121.0, 138.0), (121.8, 138.0 + dy), dxfattribs={"layer": "S-ARROW_HEAD"})
    msp.add_text(
        "W12X40 COLUMN, SEE PLAN",
        dxfattribs={"layer": LAYER_TEXT, "height": 1.5, "insert": (130.5, 149.5)},
    )

    # A bare rectangle with text inside but no interior separators. The
    # closest call in the set: it has a boundary and content, and is still
    # not a table because it has no rows or columns.
    box = [
        ((150.0, 120.0), (190.0, 120.0)),
        ((150.0, 132.0), (190.0, 132.0)),
        ((150.0, 120.0), (150.0, 132.0)),
        ((190.0, 120.0), (190.0, 132.0)),
    ]
    for start, end in box:
        msp.add_line(start, end, dxfattribs={"layer": "REVIEW_TEXT_BOX"})
    msp.add_text(
        "FOR REVIEW ONLY - NOT FOR CONSTRUCTION",
        dxfattribs={"layer": LAYER_TEXT, "height": 1.5, "insert": (151.0, 125.0)},
    )


def build(path: Path) -> None:
    # R12, matching the real drawing. It is the least forgiving target: no
    # LWPOLYLINE, no native TABLE entity, and anonymous blocks everywhere.
    doc = ezdxf.new("R12", setup=True)
    for layer in (
        LAYER_GRID,
        LAYER_TEXT,
        LAYER_TITLE,
        "S-CENTERLINE",
        "S-SECT_STEEL",
        "S-LEADER",
        "S-ARROW_HEAD",
        "REVIEW_TEXT_BOX",
    ):
        if layer not in doc.layers:
            doc.layers.add(layer)

    _add_schedule(doc)
    _add_false_positives(doc)

    doc.header["$EXTMIN"] = (0.0, 0.0, 0.0)
    doc.header["$EXTMAX"] = (200.0, 160.0, 0.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    print(f"  schedule: {len(HEADERS)} columns x {len(ROWS)} data rows, title {TITLE!r}")
    print("  decoys:   S-CENTERLINE, S-SECT_STEEL, S-LEADER, S-ARROW_HEAD, REVIEW_TEXT_BOX")


if __name__ == "__main__":
    build(
        Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/cad/SSD11-member-schedule.dxf")
    )
