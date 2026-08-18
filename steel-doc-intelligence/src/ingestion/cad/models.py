"""Intermediate representation of a CAD drawing.

Deliberately CAD-native: layers, blocks, dimensions and title-block
attributes, in model-space coordinates. `DxfLoader` translates this onto the
generic `RawDocument` IR the rest of the pipeline speaks, and keeping the two
apart is what stops CAD concepts leaking into the chunkers.

The distinction that matters most here is between text a human typed and
geometry a CAD system drew. A DIMENSION entity carries the *exact* measured
value; the number rendered next to it on the sheet is derived from that
value. Reading the entity gives a figure with no OCR error and no rounding,
which is the single biggest quality advantage a DXF has over a scan of the
same drawing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.ingestion.loaders.base import BoundingBox


@dataclass(frozen=True)
class LayoutSheet:
    """One paper-space layout, or model space itself.

    Layouts map onto pages downstream: a DXF with three paper-space layouts
    is a three-page document, which is how an engineer thinks of a drawing
    set anyway.
    """

    name: str
    index: int
    is_model_space: bool = False
    extents: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class CadTextEntity:
    """TEXT, MTEXT or a block ATTRIB.

    `layer` is kept because on a structural drawing the layer *is* the
    semantics: text on `S-DIM` is a dimension annotation, text on `TITLE` is
    title-block content, text on `S-NOTES` is a general note. That is
    structure the generic pipeline would otherwise have to guess at.
    """

    text: str
    layer: str
    layout_index: int
    entity_type: str = "TEXT"
    bbox: BoundingBox | None = None
    height: float = 0.0
    rotation: float = 0.0


@dataclass(frozen=True)
class TitleBlockField:
    """One tag/value pair from a title-block block reference.

    These are the facts a drawing register is built from -- drawing number,
    revision, project, scale, who drew and who checked it -- and in a DXF
    they are structured attributes rather than text to be parsed out of a
    corner of the sheet.
    """

    tag: str
    value: str
    block_name: str
    layout_index: int


@dataclass(frozen=True)
class CadGeometry:
    """One drawn primitive, kept with its actual coordinates.

    Geometry used to be counted and discarded, which made every question
    about *what a label refers to* unanswerable: the drawing states
    `BENT PLATE 5x5x 12 GA.` and draws a leader from that text to the plate,
    and without coordinates the two could never be joined.

    Points are model-space and in drawing units. `entity_id` is the DXF
    handle where one exists, so an association can be traced back to the
    exact entity in the source file.
    """

    entity_id: str
    entity_type: str
    layer: str
    layout_index: int
    points: tuple[tuple[float, float], ...] = ()
    #: Set when the primitive came from inside a block definition, which is
    #: where most of this drawing's content lives.
    block_name: str = ""

    @property
    def bbox(self) -> BoundingBox | None:
        if not self.points:
            return None
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys), space="model")


@dataclass(frozen=True)
class Annotation:
    """A piece of text and the thing it labels, or an honest admission that
    the drawing does not make the link determinable.

    `relation` is `leader` when a leader chain connects the two, and
    `unresolved` when no chain reaches the text or no geometry sits at the
    far end. An unresolved annotation is still recorded: "the drawing says
    this, and does not say what it refers to" is a fact about the drawing,
    and is a great deal more useful than a confident guess at the nearest
    line. On a detail sheet the nearest line to a callout is very often part
    of the leader pointing somewhere else entirely.
    """

    text: str
    text_layer: str
    layout_index: int
    relation: str = "unresolved"
    text_bbox: BoundingBox | None = None
    target_entity_id: str = ""
    target_layer: str = ""
    target_point: tuple[float, float] | None = None
    confidence: float = 0.0
    #: Why this was concluded, in words, so a wrong association can be
    #: argued with rather than merely disbelieved.
    evidence: str = ""


@dataclass(frozen=True)
class DimensionRecord:
    """A dimension, whether the file stated it or the sheet drew it.

    `text_override` is set when the drafter typed something in place of the
    measurement ("VARIES", "EQ", or a corrected figure). When present it is
    what the sheet shows, so it is what a reader would quote -- and the
    divergence between the two is itself worth recording.

    `source` separates the two kinds, and the distinction is the whole point.
    A DIMENSION entity carries the measured value: exact, no rounding, no
    interpretation. A *drawn* dimension is a number somebody typed beside
    some lines; we can report what it says and where it sits, but the file
    does not assert that the geometry measures it. Reference drawing
    SSD09.0-02 contains zero DIMENSION entities and five `3"` texts, so
    without this distinction the choice is between losing them and lying
    about them.
    """

    measurement: float
    dimension_type: str
    layer: str
    layout_index: int
    text_override: str = ""
    bbox: BoundingBox | None = None
    source: str = "entity"
    unit: str = ""
    confidence: float = 1.0
    #: Geometry the value was found beside, for citation and highlighting.
    target_entity_id: str = ""

    @property
    def has_exact_value(self) -> bool:
        """True only for a DIMENSION entity, whose number the CAD system
        measured rather than a person typed."""
        return self.source == "entity"


@dataclass(frozen=True)
class BlockDefinition:
    """A block's contents, recorded once per definition.

    R12 exports name most blocks `*U8`, `*U20`, `*X17`, and a schedule listing
    those is worse than no schedule: it looks authoritative and says nothing.
    Two things recover the meaning, and both are already in the file. A block
    containing text is named by its text -- `*U8` is the bolt note. A block
    containing only geometry is identified by its shape, so the thirteen
    separate definitions on `S-BOLTS` can be reported as the four repeated
    shapes they actually are.

    `width`/`height` are the definition's own extents in block-local units.
    They are part of the shape identity because primitive counts alone are far
    too coarse: `*U4` and `*U35` are both four lines and two solids, and one
    is an arrowhead 0.2 units wide while the other is a leader 45 units long.
    """

    name: str
    is_anonymous: bool = False
    #: entity type -> count, for the block's immediate contents.
    primitive_counts: dict[str, int] = field(default_factory=dict)
    #: Text in reading order, so a multi-line note reads as it does on paper.
    texts: tuple[str, ...] = ()
    nested: tuple[str, ...] = ()
    width: float = 0.0
    height: float = 0.0

    @property
    def label(self) -> str:
        """What to call this block in a schedule a person will read.

        A block's own text is the best label available and costs nothing to
        obtain. Only anonymous blocks need it: a drafter who named a block
        `AXARROW` has already said what it is.
        """
        if not self.is_anonymous:
            return self.name
        joined = " ".join(t.strip() for t in self.texts if t.strip())
        return joined or ""


@dataclass(frozen=True)
class ScheduleCell:
    """One cell of a drawn schedule, with where it is and what it says."""

    row: int
    column: int
    text: str
    bbox: BoundingBox | None = None
    #: Handles of the text entities that fell inside this cell.
    source_entity_ids: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class DrawnSchedule:
    """A schedule drawn as lines and text, recovered as a table.

    A drawing that says `SEE BOLT SCHEDULE` is pointing at the richest
    structure on the sheet, and in a DXF it is not a table at all: it is a
    grid of `LINE` entities with `TEXT` sitting in the gaps. Nothing in the
    file marks it as tabular.

    The hard part is not finding grids -- it is refusing the ones that are not
    schedules. Bolt gauge lines, member outlines, leader lines and review
    boxes are all grid-shaped, and on the reference drawing every one of them
    passes a "three horizontals and three verticals" test. What separates a
    schedule from them is that a schedule has *content in its cells*, which is
    why `confidence` is a geometric mean of structural and content evidence
    and neither half can carry the other.
    """

    title: str
    layout_index: int
    bbox: BoundingBox | None = None
    layers: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    cells: tuple[ScheduleCell, ...] = ()
    row_count: int = 0
    column_count: int = 0
    confidence: float = 0.0
    #: Handles of the grid lines, so a row can be traced back to the file.
    source_entity_ids: tuple[str, ...] = ()
    #: Why this was accepted, in words, so a wrong table can be argued with.
    evidence: str = ""

    def grid(self) -> list[list[str]]:
        """Rows of cell text, header first, blanks preserved."""
        table = [["" for _ in range(self.column_count)] for _ in range(self.row_count)]
        for cell in self.cells:
            if 0 <= cell.row < self.row_count and 0 <= cell.column < self.column_count:
                table[cell.row][cell.column] = cell.text
        return table

    def data_rows(self) -> list[list[str]]:
        """Everything below the header."""
        return self.grid()[1:] if self.row_count > 1 else []

    def row_cells(self, row: int) -> list[ScheduleCell]:
        return sorted((c for c in self.cells if c.row == row), key=lambda c: c.column)

    def row_bbox(self, row: int) -> BoundingBox | None:
        """The band this row occupies, so a citation can highlight one row.

        Spans the table's full width rather than only the populated cells: a
        row with a blank remarks column still occupies the whole band, and a
        highlight that stopped short of it would look like a parsing error.
        """
        boxes = [c.bbox for c in self.cells if c.row == row and c.bbox is not None]
        if not boxes or self.bbox is None:
            return None
        return BoundingBox(
            x0=self.bbox.x0,
            y0=min(b.y0 for b in boxes),
            x1=self.bbox.x1,
            y1=max(b.y1 for b in boxes),
            space="model",
        )


@dataclass(frozen=True)
class PartInstance:
    """A block reference: one placed occurrence of a part or detail.

    Counting these by block name is what answers "how many base-plate details
    are on this sheet" without anyone having tabulated them.

    `depth` is 0 for a block placed directly in the sheet and higher for one
    placed inside another block. The distinction is what stops a schedule
    double-counting: this drawing's 19 arrowheads each contain one nested
    block, and counting every INSERT the traversal walks past reports 38.
    """

    block_name: str
    layer: str
    layout_index: int
    insert_point: tuple[float, float]
    attributes: dict[str, str] = field(default_factory=dict)
    depth: int = 0


@dataclass
class CadDocument:
    layouts: list[LayoutSheet] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    texts: list[CadTextEntity] = field(default_factory=list)
    title_block: list[TitleBlockField] = field(default_factory=list)
    dimensions: list[DimensionRecord] = field(default_factory=list)
    parts: list[PartInstance] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    #: Drawn primitives with their coordinates. Populated only for entity
    #: types whose geometry is cheap to resolve; the point is association,
    #: not a rendering-fidelity copy of the drawing.
    geometry: list[CadGeometry] = field(default_factory=list)
    #: Text-to-geometry links found by `associate`, including the ones it
    #: refused to make.
    annotations: list[Annotation] = field(default_factory=list)
    #: Block definitions by name, recorded once each however often placed.
    block_definitions: list[BlockDefinition] = field(default_factory=list)
    #: Schedules recovered from drawn grids. Empty is the normal case: most
    #: detail sheets have no schedule, and finding one there would be a bug.
    schedules: list[DrawnSchedule] = field(default_factory=list)
    entity_count: int = 0
    proxy_entity_count: int = 0
    insunits: str = ""
    #: layer name -> {entity type: count}. What is actually *drawn* on each
    #: layer, as opposed to which layers the file declares. A structural
    #: drawing's layers are its semantics -- S-BOLTS, S-SECT_STEEL, S-DIMS --
    #: and a layer carrying only geometry has no text to speak for it, so
    #: without this it left no trace downstream and "what layers are present"
    #: was unanswerable from a drawing that plainly had them.
    entities_per_layer: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def populated_layers(self) -> list[str]:
        """Layers with at least one entity, most-drawn first.

        Distinct from `layers`, which is the declared table -- a template
        typically declares many more layers than a given sheet uses, and
        listing those would describe the template rather than the drawing.
        """
        return sorted(
            self.entities_per_layer,
            key=lambda name: (-sum(self.entities_per_layer[name].values()), name),
        )

    @property
    def text_entity_count(self) -> int:
        return len(self.texts) + len(self.title_block)

    @property
    def proxy_ratio(self) -> float:
        """Share of entities ezdxf could not resolve.

        High values mean the file was written by a vertical application whose
        objects are opaque here (Advance Steel, Tekla exports and the like).
        The geometry is present but its text may be locked inside proxies, so
        the drawing has to be read as an image instead.
        """
        if self.entity_count <= 0:
            return 0.0
        return self.proxy_entity_count / self.entity_count

    def texts_for_layout(self, layout_index: int) -> list[CadTextEntity]:
        return [t for t in self.texts if t.layout_index == layout_index]

    @property
    def resolved_annotations(self) -> list[Annotation]:
        return [a for a in self.annotations if a.relation != "unresolved"]

    @property
    def drawn_dimensions(self) -> list[DimensionRecord]:
        return [d for d in self.dimensions if not d.has_exact_value]

    def annotations_for_layout(self, layout_index: int) -> list[Annotation]:
        return [a for a in self.annotations if a.layout_index == layout_index]

    def parts_summary(
        self, layout_index: int | None = None, top_level_only: bool = False
    ) -> dict[str, int]:
        """Block name -> placed count, which is the raw material for a BOM.

        `top_level_only` counts placements on the sheet rather than every
        INSERT the block traversal walks past. The two differ whenever blocks
        nest: 19 arrowheads each containing one nested block are 19 symbols
        and 38 INSERTs, and a schedule that reports 38 is counting the
        drawing's internals rather than its contents.
        """
        counts: dict[str, int] = {}
        for part in self.parts:
            if layout_index is not None and part.layout_index != layout_index:
                continue
            if top_level_only and part.depth > 0:
                continue
            counts[part.block_name] = counts.get(part.block_name, 0) + 1
        return counts
