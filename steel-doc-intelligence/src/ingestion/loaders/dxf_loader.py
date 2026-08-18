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

from src.domain.value_objects.provenance import SPACE_SHEET
from src.ingestion.cad.blocks import describe_blocks
from src.ingestion.cad.dwg_converter import DwgConverter, NullDwgConverter
from src.ingestion.cad.dxf_reader import DxfReader
from src.ingestion.cad.models import CadDocument, DimensionRecord
from src.ingestion.cad.schedules import (
    detect_schedules,
    schedule_markdown,
    schedule_row_sentences,
)
from src.ingestion.cad.spatial import associate
from src.ingestion.loaders.base import (
    BoundingBox,
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
LABEL_ANNOTATION = "cad_annotation"
LABEL_SYMBOLS = "cad_symbols"
LABEL_SCHEDULE = "cad_schedule"
LABEL_LAYER_HEADING = "section_header"

# Words that mark a text as a callout about the detail rather than sheet
# furniture. Used only to decide whether an *unresolved* text is worth
# reporting as unresolved -- getting this wrong costs a line in a chunk, not
# a wrong answer.
_CALLOUT_WORDS = (
    "PLATE",
    "BOLT",
    "COLUMN",
    "BEAM",
    "PURLIN",
    "SLAB",
    "DECK",
    "WELD",
    "ANGLE",
    "STIFFENER",
    "GUSSET",
    "TYP",
    "SEE ",
    "MAX",
    "MIN",
)


def _looks_like_a_callout(text: str) -> bool:
    upper = text.upper()
    return any(word in upper for word in _CALLOUT_WORDS)


class _SheetFrame:
    """Converts this sheet's model space into normalised sheet space.

    Model space is Y-up, unbounded, and in whatever units the drafter used.
    A highlight needs 0..1 with the origin at the top left. Doing the
    conversion once, here, is what stops every consumer reinventing it --
    and reinventing the Y-flip is exactly the mistake that puts a highlight
    on the opposite side of the sheet from the thing it describes.

    Extents are the union of the drawing's declared `$EXTMIN`/`$EXTMAX` and
    the content actually located. The declared box alone is not enough: this
    drawing declares (0,0)-(116,116) and carries review boxes out to x=-114,
    which would normalise to a negative coordinate. Content alone is not
    enough either -- it ignores the sheet the drafter set up.
    """

    def __init__(self, cad: CadDocument, layout_index: int) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for geometry in cad.geometry:
            if geometry.layout_index != layout_index:
                continue
            for x, y in geometry.points:
                xs.append(x)
                ys.append(y)
        for text in cad.texts_for_layout(layout_index):
            if text.bbox is None:
                continue
            xs.extend((text.bbox.x0, text.bbox.x1))
            ys.extend((text.bbox.y0, text.bbox.y1))

        sheet = next((s for s in cad.layouts if s.index == layout_index), None)
        declared = sheet.extents if sheet is not None else None
        if declared is not None:
            xs.extend((declared[0], declared[2]))
            ys.extend((declared[1], declared[3]))

        self.usable = bool(xs) and max(xs) > min(xs) and bool(ys) and max(ys) > min(ys)
        self._x0 = min(xs) if xs else 0.0
        self._y0 = min(ys) if ys else 0.0
        self._width = (max(xs) - self._x0) if xs else 1.0
        self._height = (max(ys) - self._y0) if ys else 1.0

    def to_sheet(self, box: BoundingBox | None) -> BoundingBox | None:
        """A model-space rectangle as normalised sheet space, Y flipped."""
        if box is None or not self.usable or box.space != "model":
            return None
        x0 = (box.x0 - self._x0) / self._width
        x1 = (box.x1 - self._x0) / self._width
        # Y-up to Y-down: the top of the sheet is y=0.
        y0 = (self._y0 + self._height - box.y1) / self._height
        y1 = (self._y0 + self._height - box.y0) / self._height
        return BoundingBox(
            x0=_clamp(min(x0, x1)),
            y0=_clamp(min(y0, y1)),
            x1=_clamp(max(x0, x1)),
            y1=_clamp(max(y0, y1)),
            space=SPACE_SHEET,
        )

    def enclosing(self, boxes: list[BoundingBox | None]) -> BoundingBox | None:
        """One sheet-space rectangle covering all of them."""
        converted = [b for b in (self.to_sheet(box) for box in boxes) if b is not None]
        if not converted:
            return None
        return BoundingBox(
            x0=min(b.x0 for b in converted),
            y0=min(b.y0 for b in converted),
            x1=max(b.x1 for b in converted),
            y1=max(b.y1 for b in converted),
            space=SPACE_SHEET,
        )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _shorten(label: str, limit: int = 64) -> str:
    """Enough of a label to recognise it by, in a table cell.

    A block's label is its text, and one of this drawing's blocks is the
    350-character legal disclaimer. Whole in a cell it swamps the schedule and
    duplicates a chunk that already carries it in full.
    """
    label = " ".join(label.split())
    return label if len(label) <= limit else label[: limit - 3].rstrip() + "..."


def _dimension_note(label: str, target_entity_id: str, dimensions: list[DimensionRecord]) -> str:
    """Whether any dimension on the sheet belongs to this annotation's target.

    A drawn dimension records the entity it was found beside, and an
    annotation records the entity its leader points at. Matching the two is
    the only honest way to answer "what dimensions are associated with the
    bent plate" -- and on the reference drawing the answer is *none*, because
    the sheet's dimensions measure the bolt spacing. Stating that is a real
    answer; leaving it out invites the reader to assume the nearest numbers
    on the sheet belong to the part, which is how a 3" bolt gauge becomes a
    3" plate.
    """
    if not target_entity_id:
        return ""
    matching = [d for d in dimensions if d.target_entity_id == target_entity_id]
    if not matching:
        return (
            f'No dimension on this sheet is associated with the "{label}" '
            "annotation's target: the drawing places no dimension geometry "
            "against that entity."
        )
    shown = ", ".join(d.text_override or f"{d.measurement:g}" for d in matching)
    return (
        f'Dimensions associated with the "{label}" annotation\'s target: {shown}. '
        "These are drawn dimensions -- text placed beside dimension geometry -- "
        "not values measured by the CAD system."
    )


def _group_region(
    cad: CadDocument, definitions: tuple[str, ...], layout_index: int, frame: _SheetFrame
) -> BoundingBox | None:
    """Where one symbol is drawn, from the primitives inside its own blocks.

    Not from the placements' insert points: an insert point is an origin, not
    an extent, so a symbol placed once yields a single point whose enclosing
    box has zero area and is correctly discarded by `Region.merge`. That was
    the bug -- the symbol chunk carried no region, so the one genuinely visual
    question on the sheet had nowhere to look.

    Not from the sheet either. A crop covering the drawing is exactly the
    render-the-whole-drawing behaviour the vision fallback exists to avoid,
    and would be worse than declining. `CadGeometry.block_name`, recorded when
    block traversal was added in Phase 1, is what makes one symbol locatable
    on its own.
    """
    names = set(definitions)
    boxes = [
        geometry.bbox
        for geometry in cad.geometry
        if geometry.layout_index == layout_index and geometry.block_name in names
    ]
    return frame.enclosing([b for b in boxes if b is not None])


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
        # Association runs here rather than inside the reader so it stays
        # testable against a hand-built `CadDocument` with no ezdxf and no
        # fixture file, the same separation the reader's own docstring makes.
        report = associate(cad)
        cad.annotations = report.annotations
        cad.dimensions = [*cad.dimensions, *report.drawn_dimensions]
        schedules = detect_schedules(cad)
        cad.schedules = schedules.schedules
        for name, why in schedules.rejected:
            # Logged rather than dropped: a phase whose whole risk is
            # false positives has to be auditable when it says no.
            logger.debug("cad_schedule_rejected", grid=name, reason=why)
        return self._to_raw_document(cad, file_path)

    def _to_raw_document(self, cad: CadDocument, file_path: Path) -> RawDocument:
        text_blocks: list[TextBlock] = []
        tables: list[TableBlock] = []

        for sheet in cad.layouts:
            page = sheet.index + 1
            frame = _SheetFrame(cad, sheet.index)
            text_blocks.extend(self._title_block_for(cad, sheet.index, page))
            text_blocks.extend(self._layer_inventory_for(cad, page))
            text_blocks.extend(self._annotation_blocks_for(cad, sheet.index, page, frame))
            text_blocks.extend(self._symbol_summary_for(cad, sheet.index, page, frame))
            text_blocks.extend(self._schedule_blocks_for(cad, sheet.index, page, frame))
            tables.extend(self._schedule_tables_for(cad, sheet.index, page))
            text_blocks.extend(self._layer_blocks_for(cad, sheet.index, page, frame))

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
                # The chunk that answers "which layers are present" is
                # itself about every one of them.
                layers=list(cad.populated_layers),
            ),
        ]

    def _annotation_blocks_for(
        self, cad: CadDocument, layout_index: int, page: int, frame: _SheetFrame
    ) -> list[TextBlock]:
        """What each callout points at, in a sentence.

        This is the block that answers "what does the bent plate annotation
        refer to?" -- a question the drawing has always answered and the
        pipeline never could, because the answer was a line on the sheet
        rather than a word in it.

        Written as prose rather than a table for the same reason the title
        block is: retrieved on its own, `The annotation "BENT PLATE 5x5x 12
        GA. ..." labels a POLYLINE on layer S-SECT_STEEL_THRU` is an answer,
        where a row of coordinates needs its header to mean anything.

        Unresolved annotations are listed too, in their own block and in those
        words. A reader asking what a callout refers to is better served by
        "the drawing does not make that determinable" than by silence, and far
        better than by a guess.
        """
        annotations = [
            a
            for a in cad.annotations_for_layout(layout_index)
            # Sheet furniture -- the disclaimer, the scale note, the drawing
            # number -- is not a callout and saying it points at nothing adds
            # nothing. Those texts are already in the layer blocks.
            if a.relation != "unresolved" or _looks_like_a_callout(a.text)
        ]
        if not annotations:
            return []

        resolved = [a for a in annotations if a.relation != "unresolved"]
        unresolved = [a for a in annotations if a.relation == "unresolved"]

        blocks: list[TextBlock] = []

        # One section per callout, not one section listing them all. The
        # difference decides whether the question can be answered: a single
        # block holding all seven is long enough to be split, and the split
        # lands mid-sentence, so the chunk that surfaces for "what does the
        # bent plate annotation refer to" begins `_STEEL entity (15FE) at
        # (68.4, 49.5)` and names neither the callout nor the target. Given a
        # section each, every callout is a self-contained answer.
        dimensions = [d for d in cad.dimensions if d.layout_index == layout_index]
        for found in sorted(resolved, key=lambda a: -a.confidence):
            if found.target_point is None:
                continue
            label = found.text.strip()
            blocks.append(
                TextBlock(
                    text=f"Annotation: {label[:80]}",
                    page_number=page,
                    element_label=LABEL_LAYER_HEADING,
                    heading_level=2,
                    section=f"ANNOTATION {label[:40]}",
                )
            )
            body = [
                f'The annotation "{label}" labels a {found.target_layer} entity '
                f"({found.target_entity_id or 'unidentified'}) at "
                f"({found.target_point[0]:.1f}, {found.target_point[1]:.1f}) in model "
                f"space. The drawing connects the two with a leader line, so this is "
                f"what the annotation refers to. Confidence {found.confidence:.2f}.",
                f"Evidence: {found.evidence}.",
                _dimension_note(label, found.target_entity_id, dimensions),
            ]
            blocks.append(
                TextBlock(
                    text="\n".join(body),
                    page_number=page,
                    element_label=LABEL_ANNOTATION,
                    section=f"ANNOTATION {label[:40]}",
                    # The callout's own box, which is the tightest region
                    # this drawing offers: a citation for the bent-plate note
                    # lands on the note rather than on the sheet.
                    bbox=frame.to_sheet(found.text_bbox),
                    # Both ends of the association. A question about
                    # S-SECT_STEEL_THRU should reach the callout that
                    # labels something on it, not only the layer dump.
                    layers=sorted({a for a in (found.text_layer, found.target_layer) if a}),
                )
            )

        if unresolved:
            blocks.append(
                TextBlock(
                    text="Annotations with no determinable target",
                    page_number=page,
                    element_label=LABEL_LAYER_HEADING,
                    heading_level=2,
                    section="ANNOTATIONS",
                )
            )
            lines = [f'"{a.text.strip()}" -- {a.evidence}' for a in unresolved]
            blocks.append(
                TextBlock(
                    text=(
                        f"Annotations on sheet {page} whose target could not be "
                        "determined from the drawing. The text below is present on the "
                        "sheet, but no leader line connects it to geometry, so what it "
                        "refers to is not stated by the file:\n" + "\n".join(lines)
                    ),
                    page_number=page,
                    element_label=LABEL_ANNOTATION,
                    section="ANNOTATIONS",
                    # The region matters most on exactly this block. An
                    # annotation the extraction could not resolve is the one
                    # case where looking at the drawing might add something,
                    # and a vision fallback with no rectangle to crop has to
                    # decline -- or render the whole sheet, which is worse.
                    bbox=frame.enclosing([a.text_bbox for a in unresolved]),
                    layers=sorted({a.text_layer for a in unresolved if a.text_layer}),
                )
            )
        return blocks

    def _schedule_blocks_for(
        self, cad: CadDocument, layout_index: int, page: int, frame: _SheetFrame
    ) -> list[TextBlock]:
        """A schedule as a heading, a readable table, and one chunk per row.

        One chunk per row is the decision that matters. A schedule retrieved
        as a single blob answers "what is the member schedule" and nothing
        else: asked for the quantity of BP-1, the retriever either returns the
        whole table and lets the model hunt through it, or -- once the table
        is long enough to be split -- returns a fragment of pipe-separated
        values with no header, from which no quantity can be read at all.

        Each row is written with its column names attached, so
        `MARK BP-1 - DESCRIPTION ASTM A36 25MM THK MS PLATE - QTY 1` is
        answerable on its own. The whole table is emitted as well, because
        "what is the schedule" is a real question too, and as a `TableBlock`
        so the structure survives for a UI rather than only the prose.
        """
        schedules = [s for s in cad.schedules if s.layout_index == layout_index]
        if not schedules:
            return []

        drawing = self._drawing_number(cad, layout_index)
        blocks: list[TextBlock] = []
        for schedule in schedules:
            section = f"SCHEDULE {schedule.title[:40]}"
            layers = list(schedule.layers)
            blocks.append(
                TextBlock(
                    text=schedule.title,
                    page_number=page,
                    element_label=LABEL_LAYER_HEADING,
                    heading_level=2,
                    section=section,
                )
            )
            blocks.append(
                TextBlock(
                    text=(
                        f"{schedule.title} on sheet {page}"
                        + (f" of drawing {drawing}" if drawing else "")
                        + f", read from a grid drawn on {', '.join(layers)}. "
                        f"{schedule.row_count - 1} data row(s), "
                        f"{schedule.column_count} column(s): "
                        + ", ".join(c for c in schedule.columns if c.strip())
                        + f". Detection confidence {schedule.confidence:.2f}.\n"
                        + schedule_markdown(schedule)
                    ),
                    page_number=page,
                    element_label=LABEL_SCHEDULE,
                    section=section,
                    bbox=frame.to_sheet(schedule.bbox),
                    layers=layers,
                )
            )
            for index, sentence in enumerate(schedule_row_sentences(schedule, drawing), start=1):
                blocks.append(
                    TextBlock(
                        text=sentence,
                        page_number=page,
                        element_label=LABEL_SCHEDULE,
                        section=section,
                        # The row's own band, so a citation highlights the row
                        # rather than the whole schedule.
                        bbox=frame.to_sheet(schedule.row_bbox(index)),
                        layers=layers,
                    )
                )
        return blocks

    def _schedule_tables_for(
        self, cad: CadDocument, layout_index: int, page: int
    ) -> list[TableBlock]:
        """The structured grid, kept alongside the prose for a UI to render."""
        out: list[TableBlock] = []
        for schedule in cad.schedules:
            if schedule.layout_index != layout_index:
                continue
            grid = schedule.grid()
            if not grid:
                continue
            table = _grid_to_table(grid, page, caption=f"{schedule.title} (sheet {page})")
            if table is not None:
                table.layers = list(schedule.layers)
                out.append(table)
        return out

    @staticmethod
    def _drawing_number(cad: CadDocument, layout_index: int) -> str:
        for field in cad.title_block:
            if field.layout_index == layout_index and field.tag == "drawing_number":
                return field.value
        return ""

    def _layer_blocks_for(
        self, cad: CadDocument, layout_index: int, page: int, frame: _SheetFrame
    ) -> list[TextBlock]:
        """One block per layer, layer name as the heading.

        Grouping by layer rather than emitting one block per text entity is
        what gives the chunkers something to work with: a drawing's text is
        hundreds of short fragments, and chunked individually they carry no
        context at all. Grouped by layer, a chunk is "everything the drawing
        says about dimensions", which is a retrievable unit.
        """
        by_layer: dict[str, list[str]] = {}
        boxes_by_layer: dict[str, list[BoundingBox | None]] = {}
        for entity in cad.texts_for_layout(layout_index):
            layer_name = entity.layer or "0"
            by_layer.setdefault(layer_name, []).append(entity.text.strip())
            boxes_by_layer.setdefault(layer_name, []).append(entity.bbox)

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
                    # One rectangle enclosing everything the layer says.
                    # Coarse by construction -- a layer's text is scattered
                    # across the sheet -- but a box around part of the sheet
                    # is still more use than a citation to the whole of it.
                    bbox=frame.enclosing(boxes_by_layer.get(layer, [])),
                    layers=[layer],
                )
            )
        return blocks

    def _dimension_schedule(
        self, cad: CadDocument, layout_index: int, page: int
    ) -> TableBlock | None:
        """Dimensions, with their provenance stated in the table itself.

        A DIMENSION entity's number is *authoritative* in a way the text on a
        scanned drawing never is: it comes from the geometry rather than from
        a drafter typing, and from a file rather than from OCR.

        A drawn dimension is not. It is a number somebody typed beside some
        lines, and the `Source` column says so on every row, because a reader
        who cannot tell the two apart will treat the weaker one as the
        stronger. The reference drawing has zero DIMENSION entities and six
        drawn dimensions, so on that sheet the whole table is the weaker kind
        -- which is exactly when the distinction has to be visible.
        """
        records = [d for d in cad.dimensions if d.layout_index == layout_index]
        if not records:
            return None

        rows = [["Layer", "Type", "Value", "Shown", "Source"]]
        for record in records:
            shown = record.text_override or f"{record.measurement:g}"
            value = f"{record.measurement:g}"
            if record.unit:
                value = f"{value} {record.unit}"
            rows.append(
                [
                    record.layer,
                    record.dimension_type.replace("DIMENSION", "").strip() or "linear",
                    value,
                    shown,
                    "measured by CAD"
                    if record.has_exact_value
                    else f"drawn text beside dimension geometry (confidence {record.confidence:.2f})",
                ]
            )
        return _grid_to_table(rows, page, caption=f"Dimension schedule (sheet {page})")

    def _parts_table(self, cad: CadDocument, layout_index: int, page: int) -> TableBlock | None:
        """Distinct symbols and how often each is placed.

        What this replaces listed `*U10 x1, *U11 x1, *U12 x1` for thirty-six
        rows, every count 1, every name an R12 export artefact. It looked like
        a bill of materials and contained no information whatsoever. Grouping
        by shape collapses those thirty-six definitions to twenty-three, and
        on the bolts layer to four -- which is the difference between a list
        and a count.

        Symbols carrying text are named by their text. Their content is not
        repeated here: it is already in the annotation and layer chunks, and a
        third copy would dilute the retrieval of all three.
        """
        report = describe_blocks(cad, layout_index)
        if not report.groups:
            return None

        marks: dict[str, set[str]] = {}
        for part in cad.parts:
            if part.layout_index != layout_index:
                continue
            for tag, value in part.attributes.items():
                if "MARK" in tag.upper() or "PIECE" in tag.upper():
                    marks.setdefault(part.block_name, set()).add(value)

        # Only when something populates it. A drawing without piece-mark
        # attributes -- most detail sheets -- would otherwise carry an empty
        # column down every row, which reads as "no marks recorded" when the
        # truth is that this file has no such attributes at all.
        has_marks = bool(marks)
        header = ["Symbol", "Made of", "Size", "Placed", "Layers"]
        rows = [[*header, "Marks"] if has_marks else header]

        for group in report.groups:
            placed = str(group.placements)
            if not group.placements and group.nested_placements:
                placed = f"0 ({group.nested_placements} inside other symbols)"
            row = [
                _shorten(group.label) or "(unnamed shape)",
                ", ".join(
                    f"{count} {kind}" for kind, count in sorted(group.primitive_counts.items())
                ),
                f"{group.width:g} x {group.height:g}",
                placed,
                ", ".join(group.layers),
            ]
            if has_marks:
                row.append(
                    ", ".join(
                        sorted({m for name in group.definitions for m in marks.get(name, ())})
                    )
                )
            rows.append(row)
        return _grid_to_table(rows, page, caption=f"Symbol schedule (sheet {page})")

    def _symbol_summary_for(
        self, cad: CadDocument, layout_index: int, page: int, frame: _SheetFrame
    ) -> list[TextBlock]:
        """Per-layer symbol counts, with the claim they do *not* support.

        "13 placements of 4 distinct shapes on S-BOLTS" is true and checkable.
        "13 bolts" is neither, and it is the inference a reader will make
        unless the difference is spelled out. This drawing settles the point
        itself: it says `SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT`, so the
        count is deliberately not on the sheet, and producing one from the
        geometry would invent the exact number the drafter declined to give.
        """
        report = describe_blocks(cad, layout_index)
        if not report.per_layer:
            return []

        lines = [
            f"{layer}: {placements} block placement(s) of {shapes} distinct shape(s)"
            for layer, (placements, shapes) in sorted(
                report.per_layer.items(), key=lambda kv: (-kv[1][0], kv[0])
            )
        ]
        body = (
            f"Symbols placed on sheet {page}. The drawing defines "
            f"{report.definition_count} block(s), which group into "
            f"{report.distinct_shapes} distinct shapes once blocks made of the same "
            "primitives at the same size are recognised as repeats of one symbol:\n"
            + "\n".join(lines)
            + "\n\nA block placement is not a part count. These are counts of repeated "
            "drawn symbols, and what each symbol represents is not stated by the file. "
            "Where this drawing gives a part count it says so in words."
        )
        blocks: list[TextBlock] = [
            TextBlock(
                text="Symbols and block placements",
                page_number=page,
                element_label=LABEL_LAYER_HEADING,
                heading_level=2,
                section="SYMBOLS",
            ),
            TextBlock(
                text=body,
                page_number=page,
                element_label=LABEL_SYMBOLS,
                section="SYMBOLS",
                # Every layer this block reports a count for, so "what is on
                # S-BOLTS" reaches the count as well as the callouts.
                #
                # Deliberately no region: this describes the whole sheet, and
                # a crop of it would be a crop of the drawing.
                layers=sorted(report.per_layer),
            ),
        ]

        # One block per unnamed symbol, each locatable on its own.
        #
        # The sheet-level summary above deliberately carries no region: it
        # describes the whole drawing, and cropping it would be cropping the
        # drawing. But "what does this unlabelled symbol represent?" is a
        # question about *one* shape, and a vision fallback needs somewhere
        # specific to look or it can only decline. These are the only chunks
        # on a CAD sheet that say "the file does not state what this is" and
        # can also say where it is.
        for group in report.groups:
            if group.describes_itself or not group.placements:
                continue
            region = _group_region(cad, group.definitions, layout_index, frame)
            if region is None:
                continue
            kinds = ", ".join(
                f"{count} {kind}" for kind, count in sorted(group.primitive_counts.items())
            )
            blocks.append(
                TextBlock(
                    text=(
                        f"An unnamed symbol on layer {', '.join(group.layers) or 'unknown'}, "
                        f"drawn from {kinds}, {group.width:g} by {group.height:g} drawing "
                        f"units, placed {group.placements} time(s) on sheet {page}. "
                        "What this symbol represents is not stated by the file: a DXF "
                        "records the lines a symbol is made of, not its meaning."
                    ),
                    page_number=page,
                    element_label=LABEL_SYMBOLS,
                    section="SYMBOLS",
                    bbox=region,
                    layers=list(group.layers),
                )
            )
        return blocks


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
