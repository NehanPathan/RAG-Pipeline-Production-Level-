"""Loads DXF (and, where configured, DWG) onto the generic document IR.

The design decision worth stating: CAD does not get its own pipeline. A
drawing becomes a `RawDocument` with pages, text blocks and tables, so
chunking, embedding, retrieval, citation and governance all work on it
unchanged. What CAD gets instead is a *better* IR than a scan of the same
drawing would produce, because the structure is already there to be read:

* Paper-space layouts are pages, so a citation can say "sheet 2".
* Layers are sections. On a structural drawing the layer is the semantics --
  `S-DIM` is dimension annotation, `TITLE` is title-block content.
* Title-block attributes are structured fields, not text to be parsed out of
  a corner of the sheet.
* DIMENSION entities carry the exact measured value, with no OCR error and
  no rounding. This is the single largest quality advantage a DXF has.
* Block references are counted into a bill of materials nobody tabulated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.ingestion.cad.dwg_converter import DwgConverter, NullDwgConverter
from src.ingestion.cad.dxf_reader import DxfReader
from src.ingestion.cad.models import CadDocument
from src.ingestion.loaders.base import (
    DocumentLoader,
    RawDocument,
    TableBlock,
    TextBlock,
)
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MIMES = {
    "image/vnd.dxf",
    "application/dxf",
    "image/x-dxf",
    "image/vnd.dwg",
    "application/acad",
    "image/x-dwg",
}
SUPPORTED_EXTENSIONS = {".dxf", ".dwg"}

# Element labels are prefixed so the layout analyzer and chunkers can tell
# CAD-derived blocks apart from prose without knowing what a DXF is.
LABEL_TITLE_BLOCK = "cad_title_block"
LABEL_TEXT = "cad_text"
LABEL_LAYER_HEADING = "section_header"


class DxfLoader(DocumentLoader):
    def __init__(
        self,
        reader: DxfReader | None = None,
        converter: DwgConverter | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self._reader = reader or DxfReader()
        self._converter = converter or NullDwgConverter()
        self._work_dir = work_dir or Path("./uploads/derived")

    @property
    def name(self) -> str:
        return "dxf"

    def supports(self, mime_type: str, file_extension: str) -> bool:
        return mime_type in SUPPORTED_MIMES or file_extension.lower() in SUPPORTED_EXTENSIONS

    async def load(self, file_path: Path) -> RawDocument:
        # Parsing is synchronous and CPU-bound, and a large drawing set can
        # take tens of seconds -- same reasoning as DoclingLoader.
        return await asyncio.to_thread(self._parse, file_path)

    def _parse(self, file_path: Path) -> RawDocument:
        dxf_path = file_path
        if file_path.suffix.lower() == ".dwg":
            dxf_path = self._converter.to_dxf(file_path, self._work_dir / file_path.stem)

        cad = self._reader.read(dxf_path)
        return self._to_raw_document(cad, file_path)

    def _to_raw_document(self, cad: CadDocument, file_path: Path) -> RawDocument:
        text_blocks: list[TextBlock] = []
        tables: list[TableBlock] = []

        for sheet in cad.layouts:
            page = sheet.index + 1
            text_blocks.extend(self._title_block_for(cad, sheet.index, page))
            text_blocks.extend(self._layer_inventory_for(cad, page))
            text_blocks.extend(self._layer_blocks_for(cad, sheet.index, page))

            schedule = self._dimension_schedule(cad, sheet.index, page)
            if schedule:
                tables.append(schedule)
            bom = self._parts_table(cad, sheet.index, page)
            if bom:
                tables.append(bom)

        for grid in cad.tables:
            table = _grid_to_table(grid, page_number=1, caption="Drawing table")
            if table:
                tables.append(table)

        raw = RawDocument(
            file_path=file_path,
            file_name=file_path.name,
            mime_type="image/vnd.dxf",
            text_blocks=text_blocks,
            tables=tables,
            page_count=len(cad.layouts),
            loader_name=self.name,
        )
        raw.word_count = len(raw.full_text.split())
        logger.info(
            "dxf_load_done",
            file=str(file_path),
            pages=raw.page_count,
            blocks=len(text_blocks),
            tables=len(tables),
        )
        return raw

    def _title_block_for(self, cad: CadDocument, layout_index: int, page: int) -> list[TextBlock]:
        """Render title-block attributes as `key: value` prose.

        Kept as readable lines rather than a table because these are the
        facts most queries are actually about ("what revision is S-104?"),
        and a retrieved chunk that reads `drawing number: S-104 / revision: C`
        answers the question directly, where a table row would need the
        header to make sense of it.
        """
        fields = [f for f in cad.title_block if f.layout_index == layout_index]
        if not fields:
            return []

        # Later duplicates lose: a title block is sometimes placed twice on a
        # sheet, and the first is the one that plots.
        seen: dict[str, str] = {}
        for field in fields:
            seen.setdefault(field.tag.replace("_", " "), field.value)

        lines = [f"{tag}: {value}" for tag, value in seen.items()]
        return [
            TextBlock(
                text="\n".join(lines),
                page_number=page,
                element_label=LABEL_TITLE_BLOCK,
                section="TITLE",
            )
        ]

    def _layer_inventory_for(self, cad: CadDocument, page: int) -> list[TextBlock]:
        """What is drawn on each layer, as one retrievable block.

        A structural drawing's layers *are* its semantics: S-BOLTS, S-DIMS,
        S-SECT_STEEL say what the sheet is made of. But a layer is only
        visible downstream if it carries text, and on the reference drawing
        the bolts layer holds 26 lines, 16 polylines and 13 block references
        with no text at all. "What layers are present?" was therefore
        unanswerable from a drawing that plainly had them -- the pipeline
        refused as ungrounded, correctly, because nothing had ever written
        the evidence down.

        Emitted once per sheet, not per entity: one chunk that inventories
        the drawing, rather than thousands of meaningless primitives.
        """
        if not cad.entities_per_layer:
            return []

        lines = [
            f"{layer}: "
            + ", ".join(
                f"{count} {kind}"
                for kind, count in sorted(
                    cad.entities_per_layer[layer].items(), key=lambda kv: -kv[1]
                )
            )
            for layer in cad.populated_layers
        ]
        body = (
            f"Layers present in this drawing ({len(cad.populated_layers)}), "
            "with the entities drawn on each:\n" + "\n".join(lines)
        )
        return [
            TextBlock(
                text="Layers",
                page_number=page,
                element_label=LABEL_LAYER_HEADING,
                heading_level=2,
                section="LAYERS",
            ),
            TextBlock(
                text=body,
                page_number=page,
                element_label=LABEL_TEXT,
                section="LAYERS",
            ),
        ]

    def _layer_blocks_for(self, cad: CadDocument, layout_index: int, page: int) -> list[TextBlock]:
        """One block per layer, layer name as the heading.

        Grouping by layer rather than emitting one block per text entity is
        what gives the chunkers something to work with: a drawing's text is
        hundreds of short fragments, and chunked individually they carry no
        context at all. Grouped by layer, a chunk is "everything the drawing
        says about dimensions", which is a retrievable unit.
        """
        by_layer: dict[str, list[str]] = {}
        for entity in cad.texts_for_layout(layout_index):
            by_layer.setdefault(entity.layer or "0", []).append(entity.text.strip())

        blocks: list[TextBlock] = []
        for layer in sorted(by_layer):
            body = "\n".join(t for t in by_layer[layer] if t)
            if not body.strip():
                continue
            blocks.append(
                TextBlock(
                    text=layer,
                    page_number=page,
                    element_label=LABEL_LAYER_HEADING,
                    heading_level=2,
                    section=layer,
                )
            )
            blocks.append(
                TextBlock(
                    # Named inline as well as in `section`, because the chunk
                    # is retrieved on its text: a fragment reading `3"` twice
                    # and `1 1/2"` is unusable, while "Layer S-TEXT" in front
                    # of it says what the numbers belong to -- and makes the
                    # layer itself matchable by keyword search.
                    text=f"Layer {layer}:\n{body}",
                    page_number=page,
                    element_label=LABEL_TEXT,
                    section=layer,
                )
            )
        return blocks

    def _dimension_schedule(
        self, cad: CadDocument, layout_index: int, page: int
    ) -> TableBlock | None:
        """Exact measured values, as a table.

        Worth its own table because these numbers are *authoritative* in a
        way the text on a scanned drawing never is: they come from the
        geometry rather than from a drafter typing, and from a file rather
        than from OCR.
        """
        records = [d for d in cad.dimensions if d.layout_index == layout_index]
        if not records:
            return None

        rows = [["Layer", "Type", "Measured", "Shown"]]
        for record in records:
            rows.append(
                [
                    record.layer,
                    record.dimension_type.replace("DIMENSION", "").strip() or "linear",
                    f"{record.measurement:g}",
                    record.text_override or f"{record.measurement:g}",
                ]
            )
        return _grid_to_table(rows, page, caption=f"Dimension schedule (sheet {page})")

    def _parts_table(self, cad: CadDocument, layout_index: int, page: int) -> TableBlock | None:
        counts = cad.parts_summary(layout_index)
        if not counts:
            return None

        marks: dict[str, set[str]] = {}
        for part in cad.parts:
            if part.layout_index != layout_index:
                continue
            for tag, value in part.attributes.items():
                if "MARK" in tag.upper() or "PIECE" in tag.upper():
                    marks.setdefault(part.block_name, set()).add(value)

        rows = [["Block", "Count", "Marks"]]
        for name in sorted(counts):
            rows.append([name, str(counts[name]), ", ".join(sorted(marks.get(name, ())))])
        return _grid_to_table(rows, page, caption=f"Block schedule (sheet {page})")


def _grid_to_table(grid: list[list[str]], page_number: int, caption: str = "") -> TableBlock | None:
    """Render a cell grid as markdown, keeping the grid itself.

    The grid is preserved rather than only the markdown so a later step can
    window a long schedule by rows with the header repeated, and so the UI
    can show a real table instead of re-parsing pipes.
    """
    if not grid or not grid[0]:
        return None

    header = "| " + " | ".join(grid[0]) + " |"
    separator = "| " + " | ".join(["---"] * len(grid[0])) + " |"
    body = ["| " + " | ".join(row) + " |" for row in grid[1:]]

    return TableBlock(
        markdown="\n".join([header, separator, *body]),
        page_number=page_number,
        caption=caption,
        row_count=len(grid),
        col_count=len(grid[0]),
    )
