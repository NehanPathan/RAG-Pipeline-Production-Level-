"""Joining a drawing's words to the geometry they describe.

A structural detail says `BENT PLATE 5x5x 12 GA.` in one place and draws the
plate in another, and the only thing connecting them is a leader: a thin line
from the text to the part. Extract the text alone and you get a true sentence
about nothing in particular -- the sheet's own answer to "what is the bent
plate specification" is present, but "what does that annotation refer to" is
not, and neither is "what dimensions belong to it".

Three things make this harder than measuring distances.

**The nearest entity is usually the wrong answer.** On the reference drawing
the callout `BENT PLATE 5x5x 12 GA.` sits at (81.0, 92.8). The nearest drawn
line to it is 18 units away on `S-SECT_STEEL` -- part of the section outline
at the right-hand edge, not the plate. The plate is at (48.8, 74.4), 36 units
away, and the drawing says so by drawing a three-segment leader from the text
to it. Proximity ranks the wrong answer first; following the leader ranks the
right one. This is why the algorithm walks the chain rather than sorting by
distance.

**Leaders are not LEADER entities.** The file contains zero. What it contains
is 23 LINEs and 3 POLYLINEs on a layer called `S-LEADER`, which a person
reading the sheet perceives as arrows and a machine sees as disconnected
segments. They have to be reassembled by their shared endpoints first.

**Dimensions are not DIMENSION entities either.** Zero again. The five `3"`
texts stacked at x=61.5 are plain text beside plain tick marks on `S-DIMS`.
They can be reported, with their location and what they sit beside -- but the
file does not assert that the geometry measures 3 inches, and this module does
not either.

Where the drawing does not make a link determinable, the annotation is
recorded as `unresolved`. That is a fact about the drawing and a usable
answer; a confident guess at the nearest line is neither.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

from src.ingestion.cad.models import (
    Annotation,
    CadDocument,
    CadGeometry,
    CadTextEntity,
    DimensionRecord,
)
from src.ingestion.loaders.base import BoundingBox
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Layer-name fragments, matched case-insensitively against the layer of an
# entity. Layer naming on structural drawings is conventional rather than
# standardised, so these are substrings and not an enumeration -- `S-LEADER`,
# `LEADERS`, `A-ANNO-LEDR` all read as leaders.
_LEADER_HINTS = ("LEADER", "LEDR", "POINTER")
# Arrowheads are emphatically *not* leaders, even though they sit on the same
# line and look like part of it. Chaining them in was the first thing tried
# and it destroyed the structure it was meant to find: 133 arrowhead lines
# fused the drawing's 9 leaders into 34 fragments, and because an arrowhead
# attaches at the leader's tip, the tip stopped being a free end. Every
# callout then had exactly one free end -- the one at the text -- and
# "where does this leader point" became unanswerable for all five of them.
_ARROW_HINTS = ("ARROW", "TICK")
_DIMENSION_HINTS = ("DIM", "MEASURE")
# Layers whose geometry is scaffolding: it exists to point at, measure or
# frame the real thing, so it can never *be* what an annotation refers to.
# Dimension layers are in here for a reason that is easy to miss -- a leader
# tip often lands beside a dimension line simply because dimensions are drawn
# in the clear space around a detail, and without the exclusion `PURLIN, SEE
# PLAN` resolves to a dimension line 4.75 away instead of the purlin.
_NON_TARGET_HINTS = (
    _LEADER_HINTS
    + _ARROW_HINTS
    + _DIMENSION_HINTS
    + ("TEXT", "ANNO", "NOTE", "HATCH", "DEFPOINT", "FRAME", "BORDER", "TITLE")
)

# Tolerances in multiples of the text's own height. Text height is the right
# yardstick because it is the drawing's internal sense of scale: a sheet
# plotted at 1:5 and one at 1:50 have wildly different coordinate ranges but
# both put a callout roughly one character-height from its leader. Using
# drawing extents instead breaks on any sheet with a stray far-off entity,
# and this one has geometry out at x=1529 against a detail 100 wide.
_TEXT_ATTACH_HEIGHTS = 4.0
_TARGET_REACH_HEIGHTS = 4.0
#: Endpoints closer than this are the same point. Leader segments are drawn by
#: snapping, so shared ends coincide to within floating-point noise.
_JOIN_TOLERANCE = 0.05


@dataclass(frozen=True)
class LeaderChain:
    """Connected leader segments and the endpoints where the chain stops.

    A free end is an endpoint no other segment in the chain shares. A leader
    has two: one at the text, one at the thing being pointed at. The chain of
    ten segments on the reference sheet has three, because two callouts share
    a spine -- which is ordinary drafting and the reason this is a graph
    rather than a path.
    """

    segments: tuple[CadGeometry, ...]
    free_ends: tuple[tuple[float, float], ...]
    layout_index: int


@dataclass(frozen=True)
class AssociationReport:
    """What `associate` concluded, for diagnostics and for the ingest log."""

    annotations: list[Annotation]
    drawn_dimensions: list[DimensionRecord]
    chains: list[LeaderChain]

    @property
    def resolved(self) -> int:
        return sum(1 for a in self.annotations if a.relation != "unresolved")

    @property
    def unresolved(self) -> int:
        return sum(1 for a in self.annotations if a.relation == "unresolved")


def _matches(layer: str, hints: tuple[str, ...]) -> bool:
    upper = layer.upper()
    return any(hint in upper for hint in hints)


def _quantise(point: tuple[float, float]) -> tuple[int, int]:
    return (round(point[0] / _JOIN_TOLERANCE), round(point[1] / _JOIN_TOLERANCE))


def _point_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from a point to a line *segment*, not to the infinite line.

    The difference matters: a callout is often level with a long member whose
    infinite line passes right through it, while the member itself stops well
    short.
    """
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _distance_to_geometry(point: tuple[float, float], geometry: CadGeometry) -> float:
    px, py = point
    points = geometry.points
    if len(points) == 1:
        return math.hypot(px - points[0][0], py - points[0][1])
    best = math.inf
    for (ax, ay), (bx, by) in itertools.pairwise(points):
        best = min(best, _point_to_segment(px, py, ax, ay, bx, by))
    return best


def _distance_to_text(point: tuple[float, float], text: CadTextEntity) -> float:
    """Distance to the text's rectangle, zero when the point is inside it."""
    box = text.bbox
    if box is None:
        return math.inf
    px, py = point
    dx = max(box.x0 - px, 0.0, px - box.x1)
    dy = max(box.y0 - py, 0.0, py - box.y1)
    return math.hypot(dx, dy)


def group_text_lines(texts: list[CadTextEntity]) -> list[CadTextEntity]:
    """Rejoin a callout that was drafted as several stacked TEXT entities.

    `ALL BOLTS 3/4" DIA. A325,` / `SEE BOLT SCHEDULE FOR` / `MINIMUM BOLT
    COUNT` are three entities on the reference sheet and one sentence on the
    drawing. Only the first line has a leader, so leaving them apart both
    strands two thirds of the bolt specification as unresolved *and* hands the
    resolved third a truncated string ending in a comma.

    Lines join when they share a layer and rotation, start at the same x
    within half a character, and sit one line-pitch apart vertically. The
    merged entity keeps the top line's box grown to cover the stack, because
    that is the extent a reader sees and the extent a leader has to reach.
    """
    if len(texts) < 2:
        return list(texts)

    remaining = sorted(
        (t for t in texts if t.bbox is not None),
        key=lambda t: (t.layer, round(t.rotation, 1), -(t.bbox.y1 if t.bbox else 0.0)),
    )
    out: list[CadTextEntity] = [t for t in texts if t.bbox is None]
    used: set[int] = set()

    for i, head in enumerate(remaining):
        if i in used:
            continue
        used.add(i)
        stack = [head]
        for j in range(i + 1, len(remaining)):
            if j in used:
                continue
            nxt = remaining[j]
            last = stack[-1]
            if nxt.layer != last.layer or abs(nxt.rotation - last.rotation) > 0.5:
                continue
            box, last_box = nxt.bbox, last.bbox
            if box is None or last_box is None:
                continue
            pitch = last.height or nxt.height or 1.0
            if abs(box.x0 - last_box.x0) > pitch * 0.5:
                continue
            # Gap measured between the boxes, so it stays right for a line
            # that wraps to two rows.
            gap = last_box.y0 - box.y1
            if gap < -pitch * 0.25 or gap > pitch * 0.9:
                continue
            stack.append(nxt)
            used.add(j)

        if len(stack) == 1:
            out.append(head)
            continue
        boxes = [t.bbox for t in stack if t.bbox is not None]
        merged = BoundingBox(
            x0=min(b.x0 for b in boxes),
            y0=min(b.y0 for b in boxes),
            x1=max(b.x1 for b in boxes),
            y1=max(b.y1 for b in boxes),
            space="model",
        )
        out.append(
            CadTextEntity(
                text=" ".join(t.text.strip() for t in stack if t.text.strip()),
                layer=head.layer,
                layout_index=head.layout_index,
                entity_type=head.entity_type,
                bbox=merged,
                height=head.height,
                rotation=head.rotation,
            )
        )
    return out


class _Grid:
    """A uniform-cell spatial index over geometry.

    Comparing every free end against every primitive is O(N*M), which on a
    sheet with a few thousand entities is already wasteful and on a general
    arrangement drawing is prohibitive. Bucketing by cell and probing only the
    neighbourhood makes each lookup proportional to what is actually nearby.
    """

    def __init__(self, items: list[CadGeometry], cell: float) -> None:
        self._cell = cell if cell > 0 else 1.0
        self._buckets: dict[tuple[int, int], list[CadGeometry]] = {}
        for item in items:
            for key in self._keys_for(item):
                self._buckets.setdefault(key, []).append(item)

    def _keys_for(self, item: CadGeometry) -> set[tuple[int, int]]:
        box = item.bbox
        if box is None:
            return set()
        c = self._cell
        return {
            (i, j)
            for i in range(math.floor(box.x0 / c), math.floor(box.x1 / c) + 1)
            for j in range(math.floor(box.y0 / c), math.floor(box.y1 / c) + 1)
        }

    def near(self, point: tuple[float, float], radius: float) -> list[CadGeometry]:
        c = self._cell
        span = math.ceil(radius / c)
        cx, cy = math.floor(point[0] / c), math.floor(point[1] / c)
        seen: dict[int, CadGeometry] = {}
        for i in range(cx - span, cx + span + 1):
            for j in range(cy - span, cy + span + 1):
                for item in self._buckets.get((i, j), ()):
                    seen[id(item)] = item
        return list(seen.values())


def build_chains(segments: list[CadGeometry]) -> list[LeaderChain]:
    """Reassemble leader segments into the polylines a reader perceives.

    Segments are joined where their endpoints coincide. On the reference
    drawing 26 segments become 9 chains -- and the three-segment chain
    (48.8,74.4) -> (52.7,74.4) -> (52.7,93.3) -> (80.2,93.3) is the leader
    for the bent plate, whose text sits 0.97 units from the far end.
    """
    if not segments:
        return []

    # Union-find over segments that share a quantised endpoint.
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    at_point: dict[tuple[int, int], list[int]] = {}
    for index, segment in enumerate(segments):
        if len(segment.points) < 2:
            continue
        for end in (segment.points[0], segment.points[-1]):
            at_point.setdefault(_quantise(end), []).append(index)
    for members in at_point.values():
        for other in members[1:]:
            union(members[0], other)

    groups: dict[int, list[int]] = {}
    for index, segment in enumerate(segments):
        if len(segment.points) < 2:
            continue
        groups.setdefault(find(index), []).append(index)

    chains: list[LeaderChain] = []
    for members in groups.values():
        chain_segments = [segments[i] for i in members]
        counts: dict[tuple[int, int], int] = {}
        ends: dict[tuple[int, int], tuple[float, float]] = {}
        for segment in chain_segments:
            for end in (segment.points[0], segment.points[-1]):
                key = _quantise(end)
                counts[key] = counts.get(key, 0) + 1
                ends[key] = end
        free = tuple(ends[key] for key, count in counts.items() if count == 1)
        chains.append(
            LeaderChain(
                segments=tuple(chain_segments),
                free_ends=free,
                layout_index=chain_segments[0].layout_index,
            )
        )
    return chains


def _confidence(text_gap: float, target_gap: float, height: float) -> float:
    """How much to believe a leader association.

    Both gaps are measured in text-heights and both count against it: a leader
    that stops a long way short of its text was probably not that text's, and
    one whose tip lands nowhere near any geometry is pointing at something we
    did not capture. The reference drawing separates these cleanly -- matched
    callouts sit 0.6-0.8 text-heights from their leader end, unmatched ones
    6.6-10.8 -- so the curve does not need to be subtle, only monotonic.
    """
    if height <= 0:
        height = 1.0
    text_score = max(0.0, 1.0 - (text_gap / height) / _TEXT_ATTACH_HEIGHTS)
    target_score = max(0.0, 1.0 - (target_gap / height) / _TARGET_REACH_HEIGHTS)
    # Geometric mean, so a strong half cannot carry a weak one. A leader
    # touching its text but ending in empty space is not a 0.5 association.
    return round(math.sqrt(text_score * target_score), 3)


# A drawn dimension's text: 3", 1 1/2", 5'-6", 12.5, 250mm. Anchored so a
# part mark like `L3 1/2` cannot masquerade as a measurement, and the unit or
# a bare decimal must be present -- an unqualified integer on a drawing is far
# more often a quantity, a detail number or a bolt count.
_MEASUREMENT = re.compile(
    r"""^\s*
    (?:
      # Feet, optionally with inches: 5'-6", 5' 6 1/2"
        (?P<feet>\d+)\s*'\s*[-\s]?\s*(?P<feet_inches>\d+(?:\s+\d+/\d+)?)?\s*"?
      # Whole inches with a fraction, before the bare-fraction branch so that
      # `1 1/2"` is not read as a whole 1 followed by rubbish.
      | (?P<whole>\d+)\s+(?P<frac_n>\d+)/(?P<frac_d>\d+)\s*"
      # A bare fraction: 1/2", 3/4", 5/16"
      | (?P<bare_n>\d+)/(?P<bare_d>\d+)\s*"
      | (?P<inches>\d+)\s*"
      | (?P<metric>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m)\b
      | (?P<decimal>\d+\.\d+)
    )
    \s*(?P<trailing>.*)$
    """,
    re.VERBOSE,
)

# What may legitimately follow a dimension: `MAX.`, `(TYP.)`, `MIN. CLR`, and
# combinations. Anything else means the number was the start of a
# specification rather than a measurement -- `3" PLATE` is a part, not a size.
_QUALIFIER = re.compile(r"(?i)^(?:[\s.()]*(?:MAX|MIN|TYP|CLR|EQ|O\.?C\.?)[\s.()]*)+$")


def parse_measurement(text: str) -> tuple[float, str] | None:
    """`3"` -> (3.0, 'in'); `5'-6"` -> (66.0, 'in'); `250mm` -> (250.0, 'mm').

    Returns None when the text is not a measurement, which is the common case
    and must stay cheap. Feet-and-inches is normalised to inches so two
    dimensions on the same sheet are comparable without the caller knowing
    which notation the drafter used.
    """
    stripped = text.strip()
    if not stripped:
        return None
    match = _MEASUREMENT.match(stripped)
    if match is None:
        return None
    # Anything after the number must be a qualifier ("MAX.", "TYP.", "(TYP.)"),
    # not more content -- `3" PLATE` is a specification, not a dimension.
    trailing = match.group("trailing").strip()
    if trailing and not _QUALIFIER.match(trailing):
        return None

    if match.group("feet"):
        inches = float(match.group("feet")) * 12.0
        extra = match.group("feet_inches")
        if extra:
            parts = extra.split()
            inches += float(parts[0])
            if len(parts) > 1:
                num, den = parts[1].split("/")
                inches += float(num) / float(den)
        return (inches, "in")
    if match.group("whole"):
        return (
            float(match.group("whole"))
            + float(match.group("frac_n")) / float(match.group("frac_d")),
            "in",
        )
    if match.group("bare_n"):
        return (float(match.group("bare_n")) / float(match.group("bare_d")), "in")
    if match.group("inches"):
        return (float(match.group("inches")), "in")
    if match.group("metric"):
        return (float(match.group("metric")), match.group("unit"))
    if match.group("decimal"):
        return (float(match.group("decimal")), "")
    return None


def associate(cad: CadDocument) -> AssociationReport:
    """Link each text entity to the geometry the drawing points it at.

    Runs per layout, so model space and each paper-space sheet are resolved in
    their own coordinate frame and never compared across the two.
    """
    annotations: list[Annotation] = []
    dimensions: list[DimensionRecord] = []
    chains: list[LeaderChain] = []

    for layout in cad.layouts:
        result = _associate_layout(cad, layout.index)
        annotations.extend(result.annotations)
        dimensions.extend(result.drawn_dimensions)
        chains.extend(result.chains)

    report = AssociationReport(annotations=annotations, drawn_dimensions=dimensions, chains=chains)
    logger.info(
        "cad_spatial_association",
        chains=len(chains),
        resolved=report.resolved,
        unresolved=report.unresolved,
        drawn_dimensions=len(dimensions),
    )
    return report


def _associate_layout(cad: CadDocument, layout_index: int) -> AssociationReport:
    texts = group_text_lines([t for t in cad.texts_for_layout(layout_index) if t.bbox is not None])
    geometry = [g for g in cad.geometry if g.layout_index == layout_index and g.points]
    if not texts:
        return AssociationReport(annotations=[], drawn_dimensions=[], chains=[])

    leader_segments = [g for g in geometry if _matches(g.layer, _LEADER_HINTS)]
    targets = [g for g in geometry if not _matches(g.layer, _NON_TARGET_HINTS)]
    dim_geometry = [g for g in geometry if _matches(g.layer, _DIMENSION_HINTS)]

    # The sheet's own sense of scale. Median rather than mean: title text is
    # twice the height of a callout and a disclaimer is half it, and one
    # 12-unit-high sheet title should not set the tolerance for everything.
    heights = sorted(t.height for t in texts if t.height > 0)
    typical = heights[len(heights) // 2] if heights else 1.0

    chains = build_chains(leader_segments)
    target_grid = _Grid(targets, cell=max(typical * _TARGET_REACH_HEIGHTS, 1.0))

    # One annotation per text, not one per chain that happens to reach it.
    # Several chains can find the same callout -- dimension leaders converge
    # -- and without this the same sentence is asserted about two different
    # pieces of geometry, which is worse than asserting nothing.
    best: dict[int, Annotation] = {}
    for chain in chains:
        for annotation, text_index in _resolve_chain(chain, texts, target_grid, typical):
            current = best.get(text_index)
            if current is None or annotation.confidence > current.confidence:
                best[text_index] = annotation

    annotations: list[Annotation] = []
    dimensions: list[DimensionRecord] = []

    # Every text the leaders did not account for. Dimension-looking ones get a
    # drawn-dimension record; the rest are recorded as unresolved rather than
    # dropped or guessed at.
    dim_grid = _Grid(dim_geometry, cell=max(typical * _TARGET_REACH_HEIGHTS, 1.0))
    for index, text in enumerate(texts):
        found = best.get(index)
        if found is not None and found.relation != "unresolved":
            annotations.append(found)
            continue
        drawn = _drawn_dimension(text, dim_grid, typical)
        if drawn is not None:
            dimensions.append(drawn)
            continue
        annotations.append(
            found
            or Annotation(
                text=text.text,
                text_layer=text.layer,
                layout_index=layout_index,
                relation="unresolved",
                text_bbox=text.bbox,
                evidence="no leader reaches this text",
            )
        )

    return AssociationReport(annotations=annotations, drawn_dimensions=dimensions, chains=chains)


def _resolve_chain(
    chain: LeaderChain,
    texts: list[CadTextEntity],
    target_grid: _Grid,
    typical_height: float,
) -> list[tuple[Annotation, int]]:
    """Split a chain's free ends into the text end and the pointing end.

    A leader is directional in a reader's eye but not in the file: the DXF
    stores two endpoints with no indication which is which. What separates
    them is that one has text beside it and the other has geometry, so the
    ends are classified by what they are near rather than by any stored order.
    """
    ends: list[tuple[tuple[float, float], int | None, float]] = []
    for end in chain.free_ends:
        best_index, best_gap = None, math.inf
        for index, text in enumerate(texts):
            gap = _distance_to_text(end, text)
            if gap < best_gap:
                best_index, best_gap = index, gap
        ends.append((end, best_index, best_gap))

    reach = typical_height * _TEXT_ATTACH_HEIGHTS
    text_ends = [e for e in ends if e[1] is not None and e[2] <= reach]
    if not text_ends:
        return []

    # Ends that did not land on text are where the leader points. When every
    # end found text -- two callouts joined by a shared spine -- each end's
    # target is looked for from that end itself.
    other_ends = [e[0] for e in ends if e not in text_ends] or [e[0] for e in ends]

    out: list[tuple[Annotation, int]] = []
    for end, text_index, text_gap in text_ends:
        assert text_index is not None
        text = texts[text_index]
        height = text.height or typical_height
        tip = min(other_ends, key=lambda p: -math.dist(p, end)) if other_ends else end
        target, target_gap = _nearest_target(tip, target_grid, height)
        if target is None:
            out.append(
                (
                    Annotation(
                        text=text.text,
                        text_layer=text.layer,
                        layout_index=chain.layout_index,
                        relation="unresolved",
                        text_bbox=text.bbox,
                        target_point=tip,
                        evidence=(
                            f"leader reaches ({tip[0]:.1f}, {tip[1]:.1f}) "
                            "but no geometry sits within reach of its tip"
                        ),
                    ),
                    text_index,
                )
            )
            continue
        out.append(
            (
                Annotation(
                    text=text.text,
                    text_layer=text.layer,
                    layout_index=chain.layout_index,
                    relation="leader",
                    text_bbox=text.bbox,
                    target_entity_id=target.entity_id,
                    target_layer=target.layer,
                    target_point=tip,
                    confidence=_confidence(text_gap, target_gap, height),
                    evidence=(
                        f"leader of {len(chain.segments)} segment(s) runs from the text "
                        f"({text_gap:.2f} away) to ({tip[0]:.1f}, {tip[1]:.1f}), where a "
                        f"{target.entity_type} on {target.layer} sits {target_gap:.2f} away"
                    ),
                ),
                text_index,
            )
        )
    return out


def _nearest_target(
    tip: tuple[float, float], grid: _Grid, height: float
) -> tuple[CadGeometry | None, float]:
    reach = height * _TARGET_REACH_HEIGHTS
    best, best_gap = None, math.inf
    for candidate in grid.near(tip, reach):
        gap = _distance_to_geometry(tip, candidate)
        if gap < best_gap:
            best, best_gap = candidate, gap
    if best is None or best_gap > reach:
        return (None, math.inf)
    return (best, best_gap)


def _drawn_dimension(
    text: CadTextEntity, dim_grid: _Grid, typical_height: float
) -> DimensionRecord | None:
    """A measurement typed beside dimension geometry.

    Both halves are required. The number alone could be a quantity or a detail
    reference; the geometry alone is a line. Together, on a dimension layer,
    they are the drafter's statement of a size -- and `source="drawn"` records
    that it is the drafter's statement and not the file's.
    """
    parsed = parse_measurement(text.text)
    if parsed is None:
        return None
    box = text.bbox
    if box is None:
        return None

    height = text.height or typical_height
    centre = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
    target, gap = _nearest_target(centre, dim_grid, height)
    if target is None:
        return None

    value, unit = parsed
    return DimensionRecord(
        measurement=value,
        dimension_type="DRAWN",
        layer=text.layer,
        layout_index=text.layout_index,
        text_override=text.text.strip(),
        bbox=box,
        source="drawn",
        unit=unit,
        confidence=round(max(0.0, 1.0 - (gap / height) / _TARGET_REACH_HEIGHTS), 3),
        target_entity_id=target.entity_id,
    )
