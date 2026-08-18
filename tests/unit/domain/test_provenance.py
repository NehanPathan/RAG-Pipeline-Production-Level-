"""Regions: the rectangle a citation resolves to.

Most of these are about the two ways a region misleads. A rectangle in the
wrong coordinate convention lands nowhere near its content and looks perfectly
valid doing it; and a rectangle inherited by a chunk that was split further
bounds text the chunk no longer contains.
"""

from __future__ import annotations

import pytest

from src.domain.value_objects.provenance import (
    PRECISION_BLOCK,
    SPACE_PAGE,
    SPACE_SHEET,
    Region,
)


def region(x0=0.0, y0=0.0, x1=1.0, y1=1.0, page=1, space=SPACE_SHEET) -> Region:
    return Region(page_number=page, x0=x0, y0=y0, x1=x1, y1=y1, space=space)


class TestGeometry:
    def test_dimensions_are_derived(self):
        r = region(0.2, 0.4, 0.5, 0.9)
        assert r.width == pytest.approx(0.3)
        assert r.height == pytest.approx(0.5)
        assert r.area == pytest.approx(0.15)

    def test_touching_rectangles_overlap(self):
        assert region(0.0, 0.0, 0.5, 0.5).overlaps(region(0.4, 0.4, 0.9, 0.9))

    def test_separated_rectangles_do_not(self):
        assert not region(0.0, 0.0, 0.2, 0.2).overlaps(region(0.8, 0.8, 0.9, 0.9))

    def test_a_gap_allowance_can_bridge_them(self):
        a, b = region(0.0, 0.0, 0.2, 0.2), region(0.25, 0.0, 0.4, 0.2)
        assert not a.overlaps(b)
        assert a.overlaps(b, gap=0.1)

    def test_union_encloses_both(self):
        merged = region(0.1, 0.1, 0.2, 0.2).union(region(0.7, 0.6, 0.8, 0.9))
        assert (merged.x0, merged.y0, merged.x1, merged.y1) == (0.1, 0.1, 0.8, 0.9)


class TestCoordinateConventions:
    """A rectangle read in the wrong convention is wrong and looks fine."""

    def test_regions_on_different_pages_never_overlap(self):
        assert not region(page=1).overlaps(region(page=2))

    def test_regions_in_different_spaces_never_overlap(self):
        """Normalised 0..1 and absolute page points share a number range and
        share no meaning."""
        assert not region(space=SPACE_SHEET).overlaps(region(space=SPACE_PAGE))

    def test_merge_keeps_spaces_apart(self):
        merged = Region.merge([region(space=SPACE_SHEET), region(space=SPACE_PAGE)])
        assert len(merged) == 2
        assert {r.space for r in merged} == {SPACE_SHEET, SPACE_PAGE}

    def test_merge_keeps_pages_apart(self):
        merged = Region.merge([region(page=1), region(page=2)])
        assert len(merged) == 2


class TestMerge:
    def test_overlapping_rectangles_coalesce(self):
        merged = Region.merge([region(0.0, 0.0, 0.5, 0.5), region(0.4, 0.4, 0.9, 0.9)])
        assert len(merged) == 1
        assert merged[0].x1 == pytest.approx(0.9)

    def test_a_chain_of_overlaps_coalesces_in_one_result(self):
        """One sweep can leave two boxes that only became adjacent after
        merging, so the pass repeats until nothing more joins."""
        merged = Region.merge(
            [
                region(0.0, 0.0, 0.2, 0.1),
                region(0.6, 0.0, 0.8, 0.1),
                region(0.15, 0.0, 0.65, 0.1),
            ]
        )
        assert len(merged) == 1

    def test_distant_rectangles_stay_separate(self):
        """A section running down two columns is not well described by the box
        enclosing both, which covers the gutter and half the next column."""
        merged = Region.merge([region(0.0, 0.0, 0.4, 0.9), region(0.6, 0.0, 1.0, 0.9)])
        assert len(merged) == 2

    def test_too_many_rectangles_collapse_to_one(self):
        """A hundred rectangles is not a more precise highlight than eight; it
        is an unreadable one and a large payload on every chunk."""
        scattered = [
            region(i * 0.05, i * 0.05, i * 0.05 + 0.01, i * 0.05 + 0.01) for i in range(15)
        ]
        merged = Region.merge(scattered, max_per_page=8)
        assert len(merged) == 1
        assert merged[0].x0 == pytest.approx(0.0)

    def test_zero_area_rectangles_are_dropped(self):
        assert Region.merge([region(0.5, 0.5, 0.5, 0.5)]) == []

    def test_merging_is_deterministic(self):
        """A re-index that produced different rectangles from the same blocks
        would silently change every stored highlight."""
        blocks = [
            region(0.7, 0.1, 0.9, 0.2),
            region(0.1, 0.5, 0.3, 0.6),
            region(0.72, 0.12, 0.95, 0.25),
        ]
        assert Region.merge(blocks) == Region.merge(list(reversed(blocks)))


class TestRoundTrip:
    def test_a_region_survives_serialisation(self):
        original = region(0.12345, 0.5, 0.9, 0.95, page=3)
        assert Region.from_dict(original.to_dict()) == original

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"page_number": 1},
            {"page_number": "x", "x0": 0, "y0": 0, "x1": 1, "y1": 1},
            {"page_number": 1, "x0": None, "y0": 0, "x1": 1, "y1": 1},
        ],
    )
    def test_a_malformed_region_is_dropped_not_raised(self, payload):
        """A bad rectangle in a stored payload should cost a highlight, not an
        answer."""
        assert Region.from_dict(payload) is None

    def test_space_defaults_when_absent(self):
        rebuilt = Region.from_dict({"page_number": 1, "x0": 0, "y0": 0, "x1": 1, "y1": 1})
        assert rebuilt is not None
        assert rebuilt.space == SPACE_SHEET


def test_precision_constants_are_distinct():
    from src.domain.value_objects.provenance import PRECISION_PAGE, PRECISION_SECTION

    assert len({PRECISION_BLOCK, PRECISION_SECTION, PRECISION_PAGE}) == 3
