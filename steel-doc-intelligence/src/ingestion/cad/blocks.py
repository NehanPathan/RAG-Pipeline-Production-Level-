"""Making a drawing's blocks mean something.

The block schedule this replaces listed `*U10 x1, *U11 x1, *U12 x1` and so on
down the sheet — thirty-six rows, every count 1, every name meaningless. It
was worse than no schedule, because it looked authoritative. The names are
what an R12 export produces when it anonymises blocks, and they carry nothing
at all.

Two deterministic recoveries, both using information already in the file.

**A block containing text is named by its text.** `*U8` holds `ALL BOLTS 3/4"
DIA. A325, / SEE BOLT SCHEDULE FOR / MINIMUM BOLT COUNT`, so it is the bolt
note. This costs nothing and is right whenever it applies.

**A block containing only geometry is identified by its shape.** The thirteen
definitions on `S-BOLTS` are not thirteen different things: grouped by what
they are made of and how big they are, they are four repeated shapes. That
turns an unusable list into the beginning of a count.

What this deliberately does *not* do is name those shapes. Thirteen block
placements on a layer called `S-BOLTS` is not thirteen bolts, and this sheet
says so itself — `SEE BOLT SCHEDULE FOR MINIMUM BOLT COUNT`. The count is
deliberately not on this drawing, and inferring one from the geometry would
be inventing the exact number the drafter declined to give.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.ingestion.cad.models import BlockDefinition, CadDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

#: What identifies a shape: primitive makeup, rounded size, and any text.
Signature = tuple[tuple[tuple[str, int], ...], float, float, tuple[str, ...]]

#: Extents are rounded before comparison, because two placements of one symbol
#: differ in the last floating-point digit. Exact rounded *coordinates* were
#: tried first and fragmented badly -- `*U25`, `*U27` and `*U29` are
#: byte-identical in shape yet landed in three buckets, because one vertex sat
#: on a rounding boundary. Overall size is coarser, stable, and states a claim
#: that can be checked by looking at the drawing.
_SIZE_PLACES = 1


@dataclass(frozen=True)
class SymbolGroup:
    """Block definitions that are the same shape, and where they are placed.

    `definitions` is plural because an R12 export gives each placement of a
    symbol its own anonymous definition. Recognising that they are one shape
    is the entire point: it is the difference between "13 unknown blocks" and
    "4 repeated shapes, one of them placed 5 times".
    """

    label: str
    primitive_counts: dict[str, int] = field(default_factory=dict)
    width: float = 0.0
    height: float = 0.0
    definitions: tuple[str, ...] = ()
    #: Placements directly on a sheet -- what a reader would count.
    placements: int = 0
    #: Placements inside another block. An arrowhead's inner block is placed
    #: 19 times and appears on the drawing zero times in its own right.
    nested_placements: int = 0
    layers: tuple[str, ...] = ()

    @property
    def describes_itself(self) -> bool:
        """True when the block's own text names it, so no shape talk is needed."""
        return bool(self.label)

    def summary(self) -> str:
        kinds = ", ".join(
            f"{count} {kind}" for kind, count in sorted(self.primitive_counts.items())
        )
        if self.describes_itself:
            return f'"{self.label}"'
        return f"an unnamed shape of {kinds}, {self.width:g} by {self.height:g} drawing units"


@dataclass(frozen=True)
class BlockReport:
    groups: list[SymbolGroup]
    #: layer -> (placements, distinct shapes) for layers carrying blocks.
    per_layer: dict[str, tuple[int, int]]

    @property
    def definition_count(self) -> int:
        return sum(len(g.definitions) for g in self.groups)

    @property
    def distinct_shapes(self) -> int:
        return len(self.groups)


def signature_of(definition: BlockDefinition) -> Signature:
    """What makes two block definitions the same shape.

    Primitive makeup alone is far too coarse -- `*U4` and `*U35` are both four
    lines and two solids, and one is an arrowhead a fifth of a unit wide while
    the other is a leader forty-five units long. Size separates them.

    Text is in the signature because without it every single-line callout
    collapses together: nine unrelated blocks each holding one TEXT would be
    reported as one symbol used nine times, which would be a plain falsehood
    about the drawing.
    """
    return (
        tuple(sorted(definition.primitive_counts.items())),
        round(definition.width, _SIZE_PLACES),
        round(definition.height, _SIZE_PLACES),
        definition.texts,
    )


def describe_blocks(cad: CadDocument, layout_index: int | None = None) -> BlockReport:
    """Group the drawing's block definitions into the shapes they actually are.

    Definitions are document-level -- a block table belongs to the file, not to
    a sheet -- but placements are counted per layout when one is given, so a
    three-sheet drawing does not report every sheet's symbols on every sheet.
    """
    if not cad.block_definitions:
        return BlockReport(groups=[], per_layer={})

    placements: dict[str, int] = {}
    nested: dict[str, int] = {}
    layers: dict[str, set[str]] = {}
    for part in cad.parts:
        if layout_index is not None and part.layout_index != layout_index:
            continue
        target = nested if part.depth > 0 else placements
        target[part.block_name] = target.get(part.block_name, 0) + 1
        if part.depth == 0:
            layers.setdefault(part.block_name, set()).add(part.layer)

    buckets: dict[Signature, list[BlockDefinition]] = {}
    for definition in cad.block_definitions:
        buckets.setdefault(signature_of(definition), []).append(definition)

    groups: list[SymbolGroup] = []
    for members in buckets.values():
        names = tuple(d.name for d in members)
        # A named block in the group supplies the label: a drafter who called
        # it `AXARROW` has already said what it is.
        named = next((d for d in members if not d.is_anonymous), None)
        label = named.label if named is not None else members[0].label
        group_layers: set[str] = set()
        for name in names:
            group_layers |= layers.get(name, set())
        groups.append(
            SymbolGroup(
                label=label,
                primitive_counts=dict(members[0].primitive_counts),
                width=members[0].width,
                height=members[0].height,
                definitions=names,
                placements=sum(placements.get(n, 0) for n in names),
                nested_placements=sum(nested.get(n, 0) for n in names),
                layers=tuple(sorted(group_layers)),
            )
        )

    groups = [g for g in groups if g.placements or g.nested_placements]
    groups.sort(key=lambda g: (-g.placements, -len(g.definitions), g.label))

    per_layer: dict[str, tuple[int, int]] = {}
    for group in groups:
        for layer in group.layers:
            count, shapes = per_layer.get(layer, (0, 0))
            per_layer[layer] = (count + group.placements, shapes + 1)

    report = BlockReport(groups=groups, per_layer=per_layer)
    logger.info(
        "cad_block_analysis",
        definitions=report.definition_count,
        distinct_shapes=report.distinct_shapes,
        labelled=sum(1 for g in groups if g.describes_itself),
    )
    return report
