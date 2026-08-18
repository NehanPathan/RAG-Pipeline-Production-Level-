"""Mapping tests, built on hand-made CadDocuments -- no CAD library needed.

`DxfReader` is the only module that touches ezdxf, which is what lets every
test here construct its input directly. The real ezdxf surface is covered by
one round-trip test in test_dxf_reader.py.
"""

from pathlib import Path

import pytest

from src.ingestion.cad.dwg_converter import DwgConversionUnavailableError, NullDwgConverter
from src.ingestion.cad.models import (
    BlockDefinition,
    CadDocument,
    CadTextEntity,
    DimensionRecord,
    LayoutSheet,
    PartInstance,
    TitleBlockField,
)
from src.ingestion.loaders.dxf_loader import DxfLoader


class _FakeReader:
    def __init__(self, cad: CadDocument) -> None:
        self._cad = cad
        self.read_paths: list[Path] = []

    def read(self, file_path: Path) -> CadDocument:
        self.read_paths.append(file_path)
        return self._cad


def _cad() -> CadDocument:
    return CadDocument(
        layouts=[
            LayoutSheet(name="Model", index=0, is_model_space=True),
            LayoutSheet(name="Sheet1", index=1),
        ],
        layers=["S-BEAM", "S-DIM", "TITLE"],
        texts=[
            CadTextEntity(text="ISMB 300", layer="S-BEAM", layout_index=1),
            CadTextEntity(text="B-14", layer="S-BEAM", layout_index=1),
            CadTextEntity(text="ALL DIMS IN MM", layer="S-DIM", layout_index=1),
            CadTextEntity(text="modelspace note", layer="S-BEAM", layout_index=0),
        ],
        title_block=[
            TitleBlockField(tag="drawing_number", value="S-104", block_name="TB", layout_index=1),
            TitleBlockField(tag="revision", value="C", block_name="TB", layout_index=1),
            TitleBlockField(
                tag="title", value="ROOF FRAMING PLAN", block_name="TB", layout_index=1
            ),
        ],
        dimensions=[
            DimensionRecord(
                measurement=6000.0, dimension_type="DIMENSION", layer="S-DIM", layout_index=1
            ),
            DimensionRecord(
                measurement=2999.97,
                dimension_type="DIMENSION",
                layer="S-DIM",
                layout_index=1,
                text_override="3000",
            ),
        ],
        parts=[
            PartInstance(
                block_name="BASEPLATE",
                layer="S-DET",
                layout_index=1,
                insert_point=(0.0, 0.0),
                attributes={"MARK": "BP-1"},
            ),
            PartInstance(
                block_name="BASEPLATE", layer="S-DET", layout_index=1, insert_point=(10.0, 0.0)
            ),
        ],
        block_definitions=[
            BlockDefinition(
                name="BASEPLATE",
                primitive_counts={"LINE": 4},
                width=10.0,
                height=10.0,
            )
        ],
        entity_count=12,
    )


def _loader(cad: CadDocument | None = None) -> DxfLoader:
    return DxfLoader(reader=_FakeReader(cad or _cad()))  # type: ignore[arg-type]


class TestSupports:
    @pytest.mark.parametrize("ext", [".dxf", ".DXF", ".dwg"])
    def test_cad_extensions_are_claimed(self, ext):
        assert _loader().supports("application/octet-stream", ext)

    def test_other_formats_are_not(self):
        assert not _loader().supports("application/pdf", ".pdf")


class TestPageMapping:
    async def test_each_layout_becomes_a_page(self):
        """An engineer refers to sheets. Mapping layouts onto pages is what
        lets a citation say "sheet 2" and mean it."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        assert raw.page_count == 2

    async def test_blocks_carry_the_page_they_came_from(self):
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        sheet_pages = {b.page_number for b in raw.text_blocks}
        assert sheet_pages == {1, 2}


class TestTitleBlock:
    async def test_rendered_as_readable_key_value_lines(self):
        """A retrieved chunk reading `drawing number: S-104 / revision: C`
        answers "what revision is S-104" directly."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        block = next(b for b in raw.text_blocks if b.element_label == "cad_title_block")
        assert "drawing number: S-104" in block.text
        assert "revision: C" in block.text
        assert "title: ROOF FRAMING PLAN" in block.text


class TestLayerGrouping:
    async def test_a_layer_becomes_a_heading_and_a_body(self):
        """A drawing's text is hundreds of short fragments; chunked
        individually they carry no context. Grouped by layer, a chunk is
        "everything this drawing says about dimensions"."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        headings = [b.text for b in raw.text_blocks if b.element_label == "section_header"]
        assert "S-BEAM" in headings

        beam_body = next(
            b
            for b in raw.text_blocks
            if b.element_label == "cad_text" and b.section == "S-BEAM" and b.page_number == 2
        )
        assert "ISMB 300" in beam_body.text
        assert "B-14" in beam_body.text

    async def test_layer_is_recorded_as_the_section(self):
        """On a structural drawing the layer *is* the semantics."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        assert {b.section for b in raw.text_blocks} >= {"S-BEAM", "S-DIM", "TITLE"}


class TestDimensionSchedule:
    async def test_exact_measurements_become_a_table(self):
        """These numbers come from the geometry, not from a drafter typing and
        not from OCR -- the largest quality advantage a DXF has over a scan."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        schedule = next(t for t in raw.tables if "Dimension schedule" in t.caption)
        assert "6000" in schedule.markdown

    async def test_a_drafters_override_is_kept_alongside_the_measurement(self):
        """When the sheet shows 3000 but the geometry measures 2999.97, both
        matter: the first is what a reader would quote, the second is what is
        actually drawn."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        schedule = next(t for t in raw.tables if "Dimension schedule" in t.caption)
        assert "2999.97" in schedule.markdown
        assert "3000" in schedule.markdown


class TestSymbolSchedule:
    async def test_block_references_are_counted_into_a_schedule(self):
        """Answers "how many base-plate details are on this sheet" without
        anyone having tabulated them."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        bom = next(t for t in raw.tables if "Symbol schedule" in t.caption)
        assert "BASEPLATE" in bom.markdown
        assert "| 2 |" in bom.markdown

    async def test_piece_marks_are_carried_through(self):
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        bom = next(t for t in raw.tables if "Symbol schedule" in t.caption)
        assert "BP-1" in bom.markdown

    async def test_the_schedule_records_what_a_symbol_is_made_of(self):
        """A count with no shape behind it cannot be checked against the
        drawing."""
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        bom = next(t for t in raw.tables if "Symbol schedule" in t.caption)
        assert "4 LINE" in bom.markdown
        assert "10 x 10" in bom.markdown

    async def test_a_symbol_summary_block_states_the_per_layer_counts(self):
        raw = await _loader().load(Path("/tmp/S-104.dxf"))

        summary = next(b for b in raw.text_blocks if b.element_label == "cad_symbols")
        assert "S-DET: 2 block placement(s) of 1 distinct shape(s)" in summary.text
        assert "A block placement is not a part count" in summary.text


class TestDwgHandling:
    async def test_dwg_without_a_converter_fails_with_an_actionable_message(self):
        """Not a stack trace from a library handed a file it cannot parse."""
        loader = DxfLoader(reader=_FakeReader(_cad()), converter=NullDwgConverter())  # type: ignore[arg-type]

        with pytest.raises(DwgConversionUnavailableError, match="Export the drawing"):
            await loader.load(Path("/tmp/S-104.dwg"))

    async def test_dwg_is_converted_before_reading(self):
        converted = Path("/tmp/derived/S-104.dxf")

        class _Converter:
            name = "fake"

            def available(self) -> bool:
                return True

            def to_dxf(self, source: Path, dest_dir: Path) -> Path:
                return converted

        reader = _FakeReader(_cad())
        loader = DxfLoader(reader=reader, converter=_Converter())  # type: ignore[arg-type]

        await loader.load(Path("/tmp/S-104.dwg"))

        assert reader.read_paths == [converted]

    async def test_dxf_is_read_directly(self):
        reader = _FakeReader(_cad())
        loader = DxfLoader(reader=reader, converter=NullDwgConverter())  # type: ignore[arg-type]

        await loader.load(Path("/tmp/S-104.dxf"))

        assert reader.read_paths == [Path("/tmp/S-104.dxf")]


class TestEmptyDrawing:
    async def test_a_drawing_with_no_content_yields_no_blocks(self):
        cad = CadDocument(layouts=[LayoutSheet(name="Model", index=0, is_model_space=True)])

        raw = await _loader(cad).load(Path("/tmp/empty.dxf"))

        assert raw.text_blocks == []
        assert raw.tables == []
        assert raw.page_count == 1
