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
class DimensionRecord:
    """A DIMENSION entity's measured value.

    `text_override` is set when the drafter typed something in place of the
    measurement ("VARIES", "EQ", or a corrected figure). When present it is
    what the sheet shows, so it is what a reader would quote -- and the
    divergence between the two is itself worth recording.
    """

    measurement: float
    dimension_type: str
    layer: str
    layout_index: int
    text_override: str = ""
    bbox: BoundingBox | None = None


@dataclass(frozen=True)
class PartInstance:
    """A block reference: one placed occurrence of a part or detail.

    Counting these by block name is what answers "how many base-plate details
    are on this sheet" without anyone having tabulated them.
    """

    block_name: str
    layer: str
    layout_index: int
    insert_point: tuple[float, float]
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class CadDocument:
    layouts: list[LayoutSheet] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    texts: list[CadTextEntity] = field(default_factory=list)
    title_block: list[TitleBlockField] = field(default_factory=list)
    dimensions: list[DimensionRecord] = field(default_factory=list)
    parts: list[PartInstance] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
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

    def parts_summary(self, layout_index: int | None = None) -> dict[str, int]:
        """Block name -> placed count, which is the raw material for a BOM."""
        counts: dict[str, int] = {}
        for part in self.parts:
            if layout_index is not None and part.layout_index != layout_index:
                continue
            counts[part.block_name] = counts.get(part.block_name, 0) + 1
        return counts
