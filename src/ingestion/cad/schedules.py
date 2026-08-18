"""Recovering a drawn schedule, and refusing everything that merely looks like one.

A schedule is the richest structure on most steel sheets -- it is where marks,
sizes, quantities and weights are tabulated -- and in a DXF it is not a table.
It is a grid of `LINE` entities with `TEXT` sitting in the gaps, and nothing in
the file says "table".

Finding grids is easy. **Refusing the ones that are not schedules is the whole
problem**, and it is why this phase was held back rather than shipped with a
naive detector. On the reference drawing `SSD09.0-02.dxf`, a test of "at least
three long horizontals and three long verticals" fires on eight layers and
none of them is a table:

    S-SECT_STEEL     22 h, 26 v   the member outlines
    S-ARROW_HEAD      8 h, 26 v   arrowheads
    S-LEADER         11 h,  8 v   leader lines
    REVIEW_TEXT_BOX   6 h,  6 v   two review rectangles
    S-CENTERLINE      7 h,  4 v   bolt gauge lines at exactly 3.0 spacing

`S-CENTERLINE` is the one that settles the design. Five evenly spaced
horizontals crossed by four verticals is, geometrically, a four-row
three-column table. There is no structural test that separates it from a real
schedule, because structurally it *is* one. What separates them is that its
cells are empty: the numbers it relates to sit outside the grid entirely.

So detection requires two independent kinds of evidence and scores them as a
geometric mean, where neither can compensate for the other:

* **Structural** -- a near-closed boundary, interior separators that actually
  span the table, and a consistent grid.
* **Content** -- text inside the cells, in more than one row and more than one
  column, aligned the way table columns align.

A grid with no cell content scores zero however perfect its geometry, and a
scatter of aligned text with no grid scores zero however suggestive. A
fabricated engineering schedule is far worse than a missing one: it reads as
tabulated fact, and nobody re-derives it from the sheet.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

from src.ingestion.cad.models import (
    CadDocument,
    CadGeometry,
    CadTextEntity,
    DrawnSchedule,
    ScheduleCell,
)
from src.ingestion.loaders.base import BoundingBox
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

#: A segment counts as axis-aligned when its off-axis drift is under this
#: fraction of its length. Drafted grid lines are exact; this only absorbs
#: floating-point noise and the occasional hand-drawn line.
_AXIS_TOLERANCE = 0.01

#: Coordinates within this fraction of the table's size are the same grid line.
#: Grid lines are drawn by snapping, so duplicates coincide closely.
_MERGE_RATIO = 0.01

#: A separator must span this much of the table to be a separator. The single
#: most useful structural filter: a member outline has short internal lines
#: that stop at a flange, while a schedule's row line runs the full width.
_SPAN_RATIO = 0.8

#: Below this share of cells holding text, it is not a schedule. This is the
#: gate that rejects the bolt gauge, whose cells are perfectly formed and
#: entirely empty.
_MIN_OCCUPANCY = 0.35

#: A table needs a header and at least one data row, and at least two columns
#: -- a single column of text under a heading is a list, not a schedule.
_MIN_ROWS = 2
_MIN_COLUMNS = 2

#: Below this, the detector reports nothing rather than a doubtful table.
_MIN_CONFIDENCE = 0.5

#: Words that mark a heading as a schedule title, matched case-insensitively.
_TITLE_WORDS = ("SCHEDULE", "TABLE", "LIST", "LEGEND", "BILL OF", "BOM")


@dataclass(frozen=True)
class _Segment:
    """An axis-aligned grid line: its fixed coordinate and its extent."""

    fixed: float
    low: float
    high: float
    layer: str
    entity_id: str

    @property
    def length(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class ScheduleReport:
    schedules: list[DrawnSchedule]
    #: Candidate grids that were examined and refused, with the reason. The
    #: most important output of the module after the schedules themselves:
    #: silence about a rejection is indistinguishable from not having looked.
    rejected: list[tuple[str, str]]

    @property
    def detected(self) -> bool:
        return bool(self.schedules)


def _segments_of(geometry: list[CadGeometry]) -> tuple[list[_Segment], list[_Segment]]:
    """Split drawn primitives into horizontal and vertical grid lines."""
    horizontals: list[_Segment] = []
    verticals: list[_Segment] = []
    for item in geometry:
        points = item.points
        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if dx > dy and dy <= dx * _AXIS_TOLERANCE and dx > 0:
                horizontals.append(
                    _Segment((y0 + y1) / 2, min(x0, x1), max(x0, x1), item.layer, item.entity_id)
                )
            elif dy > dx and dx <= dy * _AXIS_TOLERANCE and dy > 0:
                verticals.append(
                    _Segment((x0 + x1) / 2, min(y0, y1), max(y0, y1), item.layer, item.entity_id)
                )
    return horizontals, verticals


def _cluster(segments: list[_Segment], tolerance: float) -> list[list[_Segment]]:
    """Group collinear segments that lie on the same grid line."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s.fixed)
    groups: list[list[_Segment]] = [[ordered[0]]]
    for segment in ordered[1:]:
        if segment.fixed - groups[-1][-1].fixed <= tolerance:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return groups


def _candidate_regions(horizontals: list[_Segment]) -> list[list[_Segment]]:
    """Stacks of horizontals that could bound one table.

    Grouped by overlapping x-range rather than by proximity alone, so two
    schedules side by side on one sheet stay separate, and a row line is only
    grouped with lines it could actually share a table with.

    Sorting first and comparing against the running group is what keeps this
    linear in the number of segments rather than quadratic -- a general
    arrangement drawing has thousands.
    """
    if not horizontals:
        return []
    ordered = sorted(horizontals, key=lambda s: (-s.length, s.fixed))
    regions: list[list[_Segment]] = []
    for segment in ordered:
        for region in regions:
            low = max(min(s.low for s in region), segment.low)
            high = min(max(s.high for s in region), segment.high)
            width = max(s.high for s in region) - min(s.low for s in region)
            if high - low >= width * _SPAN_RATIO:
                region.append(segment)
                break
        else:
            regions.append([segment])
    return [r for r in regions if len(r) >= 3]


def _texts_inside(
    texts: list[CadTextEntity], box: tuple[float, float, float, float]
) -> list[CadTextEntity]:
    x0, y0, x1, y1 = box
    found = []
    for text in texts:
        bbox = text.bbox
        if bbox is None:
            continue
        cx, cy = (bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            found.append(text)
    return found


def _spread(values: list[float]) -> float:
    """1.0 when evenly spaced, falling towards 0 as spacing varies.

    Deliberately forgiving: a real schedule often has a taller header band or
    a double-height row, and demanding perfect regularity would reject the
    genuine article to exclude a bolt gauge that the content gate rejects
    anyway.
    """
    if len(values) < 2:
        return 0.0
    gaps = [b - a for a, b in itertools.pairwise(values)]
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return 0.0
    deviation = math.sqrt(sum((g - mean) ** 2 for g in gaps) / len(gaps))
    return max(0.0, 1.0 - deviation / mean)


def _looks_like_a_title(text: str) -> bool:
    upper = text.upper()
    return any(word in upper for word in _TITLE_WORDS)


def _find_title(texts: list[CadTextEntity], x0: float, x1: float, top: float, reach: float) -> str:
    """A heading sitting just above the grid, if there is one.

    Searched above rather than anywhere nearby, because that is where a
    drafter puts it, and a title picked up from below would be the note that
    follows the table.
    """
    best, best_gap = "", math.inf
    for text in texts:
        bbox = text.bbox
        if bbox is None or not _looks_like_a_title(text.text):
            continue
        gap = bbox.y0 - top
        if gap < -0.1 or gap > reach:
            continue
        centre = (bbox.x0 + bbox.x1) / 2
        if centre < x0 - reach or centre > x1 + reach:
            continue
        if gap < best_gap:
            best, best_gap = text.text.strip(), gap
    return best


class _Candidate:
    """One grid, and everything known about whether it is a schedule."""

    def __init__(
        self,
        rows: list[list[_Segment]],
        columns: list[list[_Segment]],
        texts: list[CadTextEntity],
    ) -> None:
        self.row_lines = [g[0].fixed for g in rows]
        self.column_lines = [g[0].fixed for g in columns]
        self.rows, self.columns = rows, columns
        self.x0, self.x1 = self.column_lines[0], self.column_lines[-1]
        self.y0, self.y1 = self.row_lines[0], self.row_lines[-1]
        self.width = self.x1 - self.x0
        self.height = self.y1 - self.y0
        self.texts = texts
        self.layers = tuple(sorted({s.layer for g in (*rows, *columns) for s in g}))
        self.entity_ids = tuple(
            sorted({s.entity_id for g in (*rows, *columns) for s in g if s.entity_id})
        )

    @property
    def row_count(self) -> int:
        return len(self.row_lines) - 1

    @property
    def column_count(self) -> int:
        return len(self.column_lines) - 1

    def cells(self) -> list[ScheduleCell]:
        """Text bound to cells by containment.

        Rows are numbered from the top, the way a reader counts them, which
        means descending Y -- model space is Y-up.
        """
        out: list[ScheduleCell] = []
        for row in range(self.row_count):
            top, bottom = self.row_lines[-(row + 1)], self.row_lines[-(row + 2)]
            for column in range(self.column_count):
                left, right = self.column_lines[column], self.column_lines[column + 1]
                inside = _texts_inside(self.texts, (left, bottom, right, top))
                if not inside:
                    continue
                inside.sort(
                    key=lambda t: (-(t.bbox.y0 if t.bbox else 0), t.bbox.x0 if t.bbox else 0)
                )
                out.append(
                    ScheduleCell(
                        row=row,
                        column=column,
                        text=" ".join(t.text.strip() for t in inside if t.text.strip()),
                        bbox=BoundingBox(x0=left, y0=bottom, x1=right, y1=top, space="model"),
                        source_entity_ids=(),
                    )
                )
        return out

    def boundary_completeness(self) -> float:
        """How much of the outer rectangle is actually drawn.

        A schedule is boxed. A member outline is not, and neither is a set of
        centrelines that happen to cross.
        """
        edges = 0.0
        for group, extent, span in (
            (self.rows[0], self.width, (self.x0, self.x1)),
            (self.rows[-1], self.width, (self.x0, self.x1)),
        ):
            covered = min(max(s.high for s in group), span[1]) - max(
                min(s.low for s in group), span[0]
            )
            edges += max(0.0, covered) / extent if extent else 0.0
        for group in (self.columns[0], self.columns[-1]):
            covered = min(max(s.high for s in group), self.y1) - max(
                min(s.low for s in group), self.y0
            )
            edges += max(0.0, covered) / self.height if self.height else 0.0
        return min(1.0, edges / 4)

    def structural_score(self) -> float:
        regularity = (_spread(self.row_lines) + _spread(self.column_lines)) / 2
        # Separator count saturates: eight rows is not four times the evidence
        # of two, it is simply enough.
        size = min(1.0, (self.row_count - 1) / 3) * min(1.0, (self.column_count - 1) / 2)
        return round(self.boundary_completeness() * 0.4 + regularity * 0.3 + size * 0.3, 4)

    def content_score(self, cells: list[ScheduleCell]) -> tuple[float, str]:
        total = self.row_count * self.column_count
        if not total:
            return 0.0, "no cells"
        populated = [c for c in cells if not c.is_empty]
        if not populated:
            return 0.0, "grid contains no text at all"

        occupancy = len(populated) / total
        rows_used = len({c.row for c in populated})
        columns_used = len({c.column for c in populated})
        if rows_used < _MIN_ROWS:
            return 0.0, f"text in only {rows_used} row(s)"
        if columns_used < _MIN_COLUMNS:
            return 0.0, f"text in only {columns_used} column(s)"
        if occupancy < _MIN_OCCUPANCY:
            return 0.0, f"only {occupancy:.0%} of cells hold text"

        header = [c for c in populated if c.row == 0]
        header_evidence = len(header) / self.column_count if self.column_count else 0.0
        coverage = (rows_used / self.row_count) * (columns_used / self.column_count)
        return (
            round(occupancy * 0.4 + coverage * 0.3 + header_evidence * 0.3, 4),
            f"{len(populated)} of {total} cells hold text across "
            f"{rows_used} row(s) and {columns_used} column(s)",
        )


def detect_schedules(cad: CadDocument) -> ScheduleReport:
    """Find drawn schedules, and record every grid refused and why."""
    schedules: list[DrawnSchedule] = []
    rejected: list[tuple[str, str]] = []

    for layout in cad.layouts:
        found, refused = _detect_layout(cad, layout.index)
        schedules.extend(found)
        rejected.extend(refused)

    logger.info(
        "cad_schedule_detection",
        detected=len(schedules),
        rejected=len(rejected),
        titles=[s.title for s in schedules],
    )
    return ScheduleReport(schedules=schedules, rejected=rejected)


def _detect_layout(
    cad: CadDocument, layout_index: int
) -> tuple[list[DrawnSchedule], list[tuple[str, str]]]:
    geometry = [g for g in cad.geometry if g.layout_index == layout_index and g.points]
    texts = [t for t in cad.texts_for_layout(layout_index) if t.bbox is not None]
    if not geometry:
        return [], []

    horizontals, verticals = _segments_of(geometry)
    schedules: list[DrawnSchedule] = []
    rejected: list[tuple[str, str]] = []

    for region in _candidate_regions(horizontals):
        x0 = min(s.low for s in region)
        x1 = max(s.high for s in region)
        y0 = min(s.fixed for s in region)
        y1 = max(s.fixed for s in region)
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            continue
        name = f"grid on {'/'.join(sorted({s.layer for s in region}))} at ({x0:.0f},{y0:.0f})"

        # Row lines: horizontals in this region spanning most of its width.
        row_groups = [
            g
            for g in _cluster(region, height * _MERGE_RATIO)
            if (min(max(s.high for s in g), x1) - max(min(s.low for s in g), x0))
            >= width * _SPAN_RATIO
        ]
        # Column lines: verticals inside it spanning most of its height. The
        # span test is what rejects a member outline, whose internal lines
        # stop at a flange rather than crossing the whole section.
        inside = [
            s
            for s in verticals
            if x0 - width * _MERGE_RATIO <= s.fixed <= x1 + width * _MERGE_RATIO
            and (min(s.high, y1) - max(s.low, y0)) >= height * _SPAN_RATIO
        ]
        column_groups = _cluster(inside, width * _MERGE_RATIO)

        if len(row_groups) < 3:
            rejected.append((name, f"{len(row_groups)} full-width line(s); a table needs 3+"))
            continue
        if len(column_groups) < 3:
            rejected.append((name, f"{len(column_groups)} full-height line(s); a table needs 3+"))
            continue

        candidate = _Candidate(row_groups, column_groups, texts)
        cells = candidate.cells()
        content, content_note = candidate.content_score(cells)
        if content <= 0.0:
            rejected.append((name, content_note))
            continue

        structural = candidate.structural_score()
        # Geometric mean: neither half may carry the other. A perfect grid
        # with thin content and a dense scatter of text with a poor grid both
        # land low, which is the intent.
        confidence = round(math.sqrt(structural * content), 3)
        if confidence < _MIN_CONFIDENCE:
            rejected.append((name, f"confidence {confidence:.2f} below {_MIN_CONFIDENCE:.2f}"))
            continue

        header = candidate.cells()
        columns = tuple(
            next((c.text for c in header if c.row == 0 and c.column == i), "")
            for i in range(candidate.column_count)
        )
        title = _find_title(texts, x0, x1, y1, reach=height / max(candidate.row_count, 1))
        schedules.append(
            DrawnSchedule(
                title=title or "Schedule",
                layout_index=layout_index,
                bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, space="model"),
                layers=candidate.layers,
                columns=columns,
                cells=tuple(cells),
                row_count=candidate.row_count,
                column_count=candidate.column_count,
                confidence=confidence,
                source_entity_ids=candidate.entity_ids,
                evidence=(
                    f"{candidate.row_count} rows x {candidate.column_count} columns; "
                    f"{content_note}; boundary {candidate.boundary_completeness():.0%} drawn; "
                    f"structural {structural:.2f}, content {content:.2f}"
                ),
            )
        )

    return schedules, rejected


def schedule_row_sentences(schedule: DrawnSchedule, drawing: str = "") -> list[str]:
    """Each row as a sentence that survives being retrieved on its own.

    A row split from its header is a list of values with nothing to say what
    they mean -- `BP-1 | ASTM A36 | 1 | 38` is unreadable without knowing that
    the third figure is a quantity. Pairing each value with its column makes
    the row self-describing, which is what a retriever returns and what an
    answer quotes.
    """
    grid = schedule.grid()
    if not grid:
        return []
    headers = [h.strip() or f"Column {i + 1}" for i, h in enumerate(schedule.columns)]
    prefix = f"{schedule.title}"
    if drawing:
        prefix = f"{prefix} on drawing {drawing}"

    sentences = []
    for values in grid[1:]:
        pairs = [
            f"{headers[i]} {value.strip()}"
            for i, value in enumerate(values)
            if i < len(headers) and value.strip()
        ]
        if pairs:
            sentences.append(f"{prefix} - " + " - ".join(pairs))
    return sentences


_MEASURE = re.compile(r"^\s*[\d.]+\s*$")


def schedule_markdown(schedule: DrawnSchedule) -> str:
    """The table as markdown, header separated, blanks preserved."""
    grid = schedule.grid()
    if not grid:
        return ""
    header = grid[0]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
