"""The one place a real DXF is written and read back.

Everything else in the CAD path is tested against hand-built CadDocuments.
This file exists to catch an ezdxf API change, which no amount of mocking
can -- and it is cheap, because a synthetic DXF is written in milliseconds
and needs no fixture file checked into the repository.
"""

from pathlib import Path

import pytest

from src.ingestion.cad.dxf_reader import DxfReader, normalise_tag


@pytest.fixture(scope="module")
def drawing(tmp_path_factory) -> Path:
    """A minimal but realistic sheet: layers, text, a title block, a
    dimension, a block reference with a piece mark, and a DEFPOINTS entity
    that must not appear in the output."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.layers.add("S-BEAM")
    doc.layers.add("S-DIM")
    # DEFPOINTS already exists: ezdxf's `setup=True` creates AutoCAD's
    # standard tables, which is precisely why the reader has to exclude it.

    msp = doc.modelspace()
    msp.add_text("ISMB 300", dxfattribs={"layer": "S-BEAM", "height": 2.5}).set_placement((10, 20))
    msp.add_text("SHOULD NOT APPEAR", dxfattribs={"layer": "DEFPOINTS"}).set_placement((0, 0))
    msp.add_mtext("GENERAL NOTES\\PALL DIMS IN MM", dxfattribs={"layer": "S-DIM"}).set_location(
        (0, 50)
    )

    # A title block: a block whose attributes are the drawing's identity.
    block = doc.blocks.new(name="TITLEBLOCK")
    block.add_attdef(tag="DWG_NO", insert=(0, 0))
    block.add_attdef(tag="REV", insert=(0, 5))
    insert = msp.add_blockref("TITLEBLOCK", (100, 100))
    insert.add_auto_attribs({"DWG_NO": "S-104", "REV": "C"})

    # A part with a piece mark.
    part = doc.blocks.new(name="BASEPLATE")
    part.add_attdef(tag="MARK", insert=(0, 0))
    part_ref = msp.add_blockref("BASEPLATE", (200, 200), dxfattribs={"layer": "S-DET"})
    part_ref.add_auto_attribs({"MARK": "BP-1"})

    dim = msp.add_linear_dim(base=(0, 10), p1=(0, 0), p2=(6000, 0), dxfattribs={"layer": "S-DIM"})
    dim.render()

    path = tmp_path_factory.mktemp("cad") / "S-104.dxf"
    doc.saveas(str(path))
    return path


class TestRoundTrip:
    def test_text_entities_are_read_with_their_layer(self, drawing):
        cad = DxfReader().read(drawing)

        beam_text = [t for t in cad.texts if t.layer == "S-BEAM"]
        assert [t.text for t in beam_text] == ["ISMB 300"]

    def test_mtext_formatting_codes_are_stripped(self, drawing):
        """MTEXT embeds `\\P` for a paragraph break. Left in, the extracted
        string is neither readable nor searchable."""
        cad = DxfReader().read(drawing)

        notes = next(t for t in cad.texts if t.layer == "S-DIM")
        assert "\\P" not in notes.text
        assert "ALL DIMS IN MM" in notes.text

    def test_defpoints_layer_is_excluded(self, drawing):
        """DEFPOINTS never plots, so its text is not on the drawing and must
        not become searchable content."""
        cad = DxfReader().read(drawing)

        assert all("SHOULD NOT APPEAR" not in t.text for t in cad.texts)

    def test_title_block_attributes_become_structured_fields(self, drawing):
        cad = DxfReader().read(drawing)

        fields = {f.tag: f.value for f in cad.title_block}
        assert fields["drawing_number"] == "S-104"
        assert fields["revision"] == "C"

    def test_block_references_are_recorded_with_their_attributes(self, drawing):
        cad = DxfReader().read(drawing)

        baseplate = next(p for p in cad.parts if p.block_name == "BASEPLATE")
        assert baseplate.attributes["MARK"] == "BP-1"

    def test_dimension_measurement_is_exact(self, drawing):
        """From the geometry, not from a drafter typing and not from OCR."""
        cad = DxfReader().read(drawing)

        assert cad.dimensions, "the linear dimension should have been read"
        assert cad.dimensions[0].measurement == pytest.approx(6000.0, abs=0.01)

    def test_layouts_are_enumerated_with_model_space_first(self, drawing):
        cad = DxfReader().read(drawing)

        assert cad.layouts[0].is_model_space
        assert cad.layouts[0].index == 0

    def test_text_bbox_is_marked_as_model_space(self, drawing):
        """Model space is Y-up and unbounded; page space is Y-down and
        bounded. A rectangle from one read as the other lands nowhere near
        the thing it describes."""
        cad = DxfReader().read(drawing)

        beam_text = next(t for t in cad.texts if t.layer == "S-BEAM")
        assert beam_text.bbox is not None
        assert beam_text.bbox.space == "model"


class TestNormaliseTag:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("DWG_NO", "drawing_number"),
            ("Dwg No", "drawing_number"),
            ("DRAWING NUMBER", "drawing_number"),
            ("REV", "revision"),
            ("JOB_NO", "project_number"),
            ("CHECKED BY", "checked_by"),
        ],
    )
    def test_template_spellings_agree(self, raw, expected):
        """Every template set punctuates these differently."""
        assert normalise_tag(raw) == expected

    def test_an_unknown_tag_passes_through_lowercased(self):
        assert normalise_tag("CUSTOM_FIELD") == "custom_field"
