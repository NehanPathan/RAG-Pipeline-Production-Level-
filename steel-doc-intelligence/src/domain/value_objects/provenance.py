"""Where on the page an answer came from.

A citation that resolves to "page 1" of an engineering drawing points at the
whole sheet. On a detail sheet that is thousands of entities and a dozen
callouts, so the citation identifies the document and nothing within it: a
reader who doubts the answer has no way to check it, and a reader who trusts
it has no way to find what it refers to.

A region narrows that to a rectangle. It is the difference between "this
drawing says the bent plate is 5x5x12 GA." and "*here* is where it says so".

Two conventions travel with the coordinates, because getting them confused
puts the highlight somewhere the content is not:

* `sheet` -- normalised 0..1, origin at the **top left**, Y increasing
  downwards. What a renderer can draw on directly, whatever the drawing's own
  units or scale were. CAD regions use this, converted from model space at
  ingestion.
* `page` -- absolute page units (points), Y-down. What a PDF parser reports.
* `image` -- absolute pixels in a rendered raster, Y-down.

CAD model space is neither: it is Y-**up**, unbounded, and in drawing units
that may be inches, millimetres or nothing in particular. A rectangle from one
convention read as another lands nowhere near the thing it describes, which is
why `space` is not optional in practice even though it has a default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Normalised, renderable, origin top-left.
SPACE_SHEET = "sheet"
#: Absolute page units as a PDF parser reports them.
SPACE_PAGE = "page"
#: Absolute pixels in a rendered raster.
SPACE_IMAGE = "image"

#: How precisely a chunk's regions locate it.
PRECISION_BLOCK = "block"
PRECISION_SECTION = "section"
PRECISION_PAGE = "page"


@dataclass(frozen=True)
class Region:
    """One rectangle on one page."""

    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    space: str = SPACE_SHEET

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)

    def overlaps(self, other: Region, gap: float = 0.0) -> bool:
        """True when the two touch, or come within `gap` of doing so."""
        if self.page_number != other.page_number or self.space != other.space:
            return False
        return not (
            self.x1 + gap < other.x0
            or other.x1 + gap < self.x0
            or self.y1 + gap < other.y0
            or other.y1 + gap < self.y0
        )

    def union(self, other: Region) -> Region:
        return Region(
            page_number=self.page_number,
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
            space=self.space,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "x0": round(self.x0, 5),
            "y0": round(self.y0, 5),
            "x1": round(self.x1, 5),
            "y1": round(self.y1, 5),
            "space": self.space,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Region | None:
        """Rebuild from a stored payload, or None if it is not a region.

        Returns None rather than raising: a malformed region in a payload
        should cost a highlight, not an answer.
        """
        try:
            return cls(
                page_number=int(str(data["page_number"])),
                x0=float(str(data["x0"])),
                y0=float(str(data["y0"])),
                x1=float(str(data["x1"])),
                y1=float(str(data["y1"])),
                space=str(data.get("space", SPACE_SHEET)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def merge(cls, regions: Sequence[Region], max_per_page: int = 8) -> list[Region]:
        """Coalesce touching rectangles, and cap how many survive per page.

        A chunk spans many blocks, so it needs a list of rectangles rather
        than one: a section running down two columns is not well described by
        the box enclosing both, which would cover the gutter and half the
        neighbouring text.

        The cap is what stops that argument running away. A hundred rectangles
        is not a more precise highlight than eight, it is an unreadable one and
        a large payload on every chunk. Past the cap the page's rectangles are
        replaced by the single box enclosing them, which is less precise and
        says so -- the caller downgrades `region_precision` to match.
        """
        by_page: dict[tuple[int, str], list[Region]] = {}
        for region in regions:
            if region.area <= 0 and region.width <= 0 and region.height <= 0:
                continue
            by_page.setdefault((region.page_number, region.space), []).append(region)

        out: list[Region] = []
        for key in sorted(by_page):
            merged = _coalesce(by_page[key])
            if len(merged) > max_per_page:
                enclosing = merged[0]
                for region in merged[1:]:
                    enclosing = enclosing.union(region)
                merged = [enclosing]
            out.extend(merged)
        return out


def _coalesce(regions: list[Region]) -> list[Region]:
    """Repeatedly union rectangles that touch, until nothing more merges."""
    # Sorted so the sweep is deterministic: the same blocks in the same order
    # must always produce the same rectangles, or a re-index silently changes
    # every stored highlight.
    remaining = sorted(regions, key=lambda r: (r.y0, r.x0, r.y1, r.x1))
    merged: list[Region] = []
    for region in remaining:
        for index, existing in enumerate(merged):
            if existing.overlaps(region):
                merged[index] = existing.union(region)
                break
        else:
            merged.append(region)

    # One pass can leave two boxes that only became adjacent after merging.
    if len(merged) < len(remaining):
        return _coalesce(merged)
    return merged
