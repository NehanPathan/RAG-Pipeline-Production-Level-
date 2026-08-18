"""Model space to sheet space: the conversion behind every CAD highlight.

The failure this guards against is silent. A rectangle converted with the Y
flip missed is a perfectly valid rectangle on the opposite side of the sheet
from the thing it describes, and nothing downstream can tell.
"""

from __future__ import annotations

import pytest

from src.domain.value_objects.provenance import SPACE_SHEET
from src.ingestion.cad.models import CadDocument, CadGeometry, CadTextEntity, LayoutSheet
from src.ingestion.loaders.base import BoundingBox
from src.ingestion.loaders.dxf_loader import _SheetFrame


def model_box(x0: float, y0: float, x1: float, y1: float) -> BoundingBox:
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, space="model")


def drawing(
    *,
    texts: list[CadTextEntity] | None = None,
    geometry: list[CadGeometry] | None = None,
    extents: tuple[float, float, float, float] | None = None,
) -> CadDocument:
    return CadDocument(
        layouts=[LayoutSheet(name="Model", index=0, is_model_space=True, extents=extents)],
        texts=texts or [],
        geometry=geometry or [],
    )


def square_sheet() -> CadDocument:
    """A 100x100 sheet, so a converted coordinate is readable by eye."""
    return drawing(
        geometry=[
            CadGeometry(
                entity_id="G",
                entity_type="LINE",
                layer="S-SECT_STEEL",
                layout_index=0,
                points=((0.0, 0.0), (100.0, 100.0)),
            )
        ]
    )


class TestTheYFlip:
    def test_the_top_of_model_space_becomes_the_top_of_the_sheet(self):
        """Model space is Y-up and a highlight is Y-down. Something at the top
        of the drawing must come out near y=0, not near y=1."""
        frame = _SheetFrame(square_sheet(), 0)
        converted = frame.to_sheet(model_box(0.0, 90.0, 10.0, 100.0))
        assert converted is not None
        assert converted.y0 == pytest.approx(0.0)
        assert converted.y1 == pytest.approx(0.1)

    def test_the_bottom_of_model_space_becomes_the_bottom_of_the_sheet(self):
        frame = _SheetFrame(square_sheet(), 0)
        converted = frame.to_sheet(model_box(0.0, 0.0, 10.0, 10.0))
        assert converted is not None
        assert converted.y0 == pytest.approx(0.9)
        assert converted.y1 == pytest.approx(1.0)

    def test_x_is_not_flipped(self):
        frame = _SheetFrame(square_sheet(), 0)
        converted = frame.to_sheet(model_box(0.0, 0.0, 10.0, 10.0))
        assert converted is not None
        assert converted.x0 == pytest.approx(0.0)
        assert converted.x1 == pytest.approx(0.1)


class TestNormalisation:
    def test_everything_lands_inside_the_unit_square(self):
        frame = _SheetFrame(square_sheet(), 0)
        for box in (
            model_box(0.0, 0.0, 100.0, 100.0),
            model_box(40.0, 40.0, 60.0, 60.0),
            model_box(99.0, 99.0, 100.0, 100.0),
        ):
            converted = frame.to_sheet(box)
            assert converted is not None
            assert 0.0 <= converted.x0 <= converted.x1 <= 1.0
            assert 0.0 <= converted.y0 <= converted.y1 <= 1.0

    def test_the_result_is_labelled_sheet_space(self):
        """Normalised 0..1 and absolute page points share a number range and
        share no meaning, so the label is what keeps them apart."""
        frame = _SheetFrame(square_sheet(), 0)
        converted = frame.to_sheet(model_box(0.0, 0.0, 10.0, 10.0))
        assert converted is not None
        assert converted.space == SPACE_SHEET

    def test_a_box_outside_the_declared_extents_is_still_included(self):
        """The reference drawing declares (0,0)-(116,116) and carries review
        boxes out to x=-114. Normalising against the declaration alone would
        put them at a negative coordinate."""
        cad = drawing(
            geometry=[
                CadGeometry(
                    entity_id="G",
                    entity_type="LINE",
                    layer="S-MISC",
                    layout_index=0,
                    points=((-100.0, 0.0), (100.0, 100.0)),
                )
            ],
            extents=(0.0, 0.0, 100.0, 100.0),
        )
        frame = _SheetFrame(cad, 0)
        converted = frame.to_sheet(model_box(-100.0, 0.0, -90.0, 10.0))
        assert converted is not None
        assert converted.x0 == pytest.approx(0.0)
        assert 0.0 <= converted.x1 <= 1.0

    def test_declared_extents_widen_the_frame(self):
        """Content alone ignores the sheet the drafter set up."""
        cad = drawing(
            geometry=[
                CadGeometry(
                    entity_id="G",
                    entity_type="LINE",
                    layer="S-MISC",
                    layout_index=0,
                    points=((0.0, 0.0), (50.0, 50.0)),
                )
            ],
            extents=(0.0, 0.0, 100.0, 100.0),
        )
        frame = _SheetFrame(cad, 0)
        converted = frame.to_sheet(model_box(0.0, 0.0, 50.0, 50.0))
        assert converted is not None
        # Half the declared sheet, not all of the content's own box.
        assert converted.x1 == pytest.approx(0.5)


class TestRefusal:
    def test_a_page_space_box_is_never_converted(self):
        """Converting an already-normalised rectangle again would shrink it to
        a speck in the corner."""
        frame = _SheetFrame(square_sheet(), 0)
        assert frame.to_sheet(BoundingBox(x0=0, y0=0, x1=1, y1=1, space="page")) is None

    def test_no_box_gives_no_region(self):
        frame = _SheetFrame(square_sheet(), 0)
        assert frame.to_sheet(None) is None

    def test_a_degenerate_sheet_converts_nothing(self):
        """A drawing with no located content has no frame to normalise
        against, and inventing one would put every region at (0,0)."""
        frame = _SheetFrame(drawing(), 0)
        assert frame.usable is False
        assert frame.to_sheet(model_box(0.0, 0.0, 10.0, 10.0)) is None


class TestEnclosing:
    def test_scattered_boxes_give_one_covering_rectangle(self):
        frame = _SheetFrame(square_sheet(), 0)
        enclosing = frame.enclosing(
            [model_box(0.0, 0.0, 10.0, 10.0), model_box(90.0, 90.0, 100.0, 100.0)]
        )
        assert enclosing is not None
        assert enclosing.x0 == pytest.approx(0.0)
        assert enclosing.x1 == pytest.approx(1.0)
        assert enclosing.y0 == pytest.approx(0.0)
        assert enclosing.y1 == pytest.approx(1.0)

    def test_boxes_that_cannot_be_converted_are_skipped(self):
        frame = _SheetFrame(square_sheet(), 0)
        enclosing = frame.enclosing([None, model_box(0.0, 0.0, 10.0, 10.0)])
        assert enclosing is not None
        assert enclosing.x1 == pytest.approx(0.1)

    def test_nothing_convertible_gives_nothing(self):
        frame = _SheetFrame(square_sheet(), 0)
        assert frame.enclosing([None, None]) is None
