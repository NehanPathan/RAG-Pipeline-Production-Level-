"""The only module that imports ezdxf.

Confined deliberately, for the same reason `TesseractProvider` takes an
injected `image_to_data`: every other module can then be unit-tested against
a hand-built `CadDocument` with no CAD library, no fixture files and no
version coupling. One round-trip test covers the ezdxf surface itself, and
it is the thing that catches an API change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ingestion.cad.models import (
    BlockDefinition,
    CadDocument,
    CadGeometry,
    CadTextEntity,
    DimensionRecord,
    LayoutSheet,
    PartInstance,
    TitleBlockField,
)
from src.ingestion.loaders.base import BoundingBox
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_TEXT_TYPES = {"TEXT", "MTEXT"}

# Entity types whose coordinates are cheap to resolve and worth keeping for
# association. Everything else is still counted per layer, just not located:
# the goal is to answer "what does this label point at", not to reproduce the
# drawing.
_GEOMETRY_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "SOLID", "TRACE"}

#: An affine transform as (a, b, c, d, e, f): x' = a*x + c*y + e.
Transform = tuple[float, float, float, float, float, float]
_IDENTITY: Transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(t: Transform, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = t
    return (a * x + c * y + e, b * x + d * y + f)


def _compose(outer: Transform, inner: Transform) -> Transform:
    """`inner` then `outer` -- the block-local frame mapped out one level."""
    a, b, c, d, e, f = inner
    oa, ob, oc, od, oe, of = outer
    return (
        a * oa + b * oc,
        a * ob + b * od,
        c * oa + d * oc,
        c * ob + d * od,
        e * oa + f * oc + oe,
        e * ob + f * od + of,
    )


def _scale_of(t: Transform) -> float:
    """Uniform-ish scale, used to carry text height through a scaled block."""
    a, b, c, d, _, _ = t
    sx = (a * a + b * b) ** 0.5
    sy = (c * c + d * d) ** 0.5
    return (sx + sy) / 2 or 1.0


# Layers that carry construction geometry rather than content. DEFPOINTS is
# AutoCAD's non-plotting layer for dimension definition points, so anything
# on it is invisible on the printed sheet and should not become searchable
# text.
_DEFAULT_IGNORED_LAYERS = ("DEFPOINTS",)

# Title-block attribute tags, as they are actually spelled across template
# sets. Matched case-insensitively after stripping separators, so DWG_NO,
# "DWG NO" and DwgNo all land on the same field.
_TITLE_BLOCK_TAGS = {
    "DWGNO": "drawing_number",
    "DRAWINGNO": "drawing_number",
    "DRAWINGNUMBER": "drawing_number",
    "SHEETNO": "sheet_number",
    "SHEET": "sheet_number",
    "REV": "revision",
    "REVISION": "revision",
    "TITLE": "title",
    "DRAWINGTITLE": "title",
    "PROJECT": "project",
    "PROJECTNO": "project_number",
    "JOBNO": "project_number",
    "CLIENT": "client",
    "SCALE": "scale",
    "DATE": "date",
    "DRAWN": "drawn_by",
    "DRAWNBY": "drawn_by",
    "CHECKED": "checked_by",
    "CHECKEDBY": "checked_by",
    "APPROVED": "approved_by",
}


def normalise_tag(tag: str) -> str:
    """`DWG_NO` / `Dwg No` -> `drawing_number`; unknown tags pass through lowercased."""
    key = "".join(ch for ch in tag.upper() if ch.isalnum())
    return _TITLE_BLOCK_TAGS.get(key, tag.strip().lower())


class DxfReader:
    def __init__(
        self,
        max_entities: int = 500_000,
        ignored_layers: tuple[str, ...] = _DEFAULT_IGNORED_LAYERS,
        max_block_depth: int = 8,
    ) -> None:
        self._max_entities = max_entities
        self._ignored = {layer.upper() for layer in ignored_layers}
        # How far to follow INSERT -> block -> INSERT. Real drawings nest a
        # few levels; the cap exists because a malformed file can reference
        # itself, and a self-referencing block would otherwise recurse until
        # the process died.
        self._max_block_depth = max_block_depth
        # Set per `read()`; the block table is what INSERTs resolve against.
        self._blocks: Any = None
        # Definitions already described, so a block placed 19 times is
        # recorded once.
        self._seen_definitions: set[str] = set()

    def read(self, file_path: Path) -> CadDocument:
        """Parse a DXF. Synchronous and CPU-bound; the loader runs it in a thread."""
        import ezdxf

        # `readfile` is ezdxf's documented entry point; its package just does
        # not declare an explicit re-export for the type checker to follow.
        doc = ezdxf.readfile(str(file_path))  # type: ignore[attr-defined]
        # Block definitions are needed to follow INSERTs into their contents.
        self._blocks = doc.blocks
        self._seen_definitions = set()
        cad = CadDocument(
            layers=sorted(layer.dxf.name for layer in doc.layers),
            insunits=str(doc.header.get("$INSUNITS", "")),
        )

        # Model space is page 1, then each paper-space layout in order. An
        # engineer refers to sheets, and this is the mapping that makes a
        # citation say "sheet 2" and mean it.
        spaces: list[tuple[LayoutSheet, Any]] = []
        model = doc.modelspace()
        spaces.append(
            (
                LayoutSheet(
                    name="Model",
                    index=0,
                    is_model_space=True,
                    extents=self._declared_extents(doc),
                ),
                model,
            )
        )
        for layout in doc.layouts:
            if layout.name.lower() == "model":
                continue
            spaces.append((LayoutSheet(name=layout.name, index=len(spaces)), layout))
        cad.layouts = [sheet for sheet, _ in spaces]

        for sheet, space in spaces:
            self._read_space(cad, sheet.index, space)
            if cad.entity_count >= self._max_entities:
                logger.warning(
                    "dxf_entity_cap_reached",
                    file=str(file_path),
                    cap=self._max_entities,
                )
                break

        logger.info(
            "dxf_read",
            file=str(file_path),
            layouts=len(cad.layouts),
            texts=len(cad.texts),
            dimensions=len(cad.dimensions),
            parts=len(cad.parts),
            block_definitions=len(cad.block_definitions),
            geometry=len(cad.geometry),
            proxy_ratio=round(cad.proxy_ratio, 3),
        )
        return cad

    def _declared_extents(self, doc: Any) -> tuple[float, float, float, float] | None:
        """The drawing's own statement of its extents, from the header.

        Worth preferring over a computed bounding box because it is what the
        CAD system considers the sheet, and because a computed box is at the
        mercy of one stray entity. It is not sufficient on its own either --
        this file declares (0,0)-(116,116) while carrying review boxes out to
        x=-114 -- so the consumer unions the two.
        """
        try:
            low = doc.header.get("$EXTMIN")
            high = doc.header.get("$EXTMAX")
            if low is None or high is None:
                return None
            extents = (float(low[0]), float(low[1]), float(high[0]), float(high[1]))
        except Exception:  # pragma: no cover - malformed header
            return None
        if extents[2] <= extents[0] or extents[3] <= extents[1]:
            return None
        return extents

    def _read_space(
        self,
        cad: CadDocument,
        layout_index: int,
        space: Any,
        depth: int = 0,
        seen_blocks: frozenset[str] = frozenset(),
        transform: Transform = _IDENTITY,
        block_name: str = "",
    ) -> None:
        for entity in space:
            cad.entity_count += 1
            if cad.entity_count > self._max_entities:
                return

            dxftype = entity.dxftype()
            if dxftype in ("ACAD_PROXY_ENTITY", "ACAD_PROXY_OBJECT"):
                cad.proxy_entity_count += 1
                continue

            layer = str(getattr(entity.dxf, "layer", ""))
            if layer.upper() in self._ignored:
                continue

            # Count what is drawn on each layer, whether or not it is text.
            # A layer holding only geometry -- S-BOLTS is 26 lines, 16
            # polylines and 13 block references with not one character of
            # text on it -- produces no text and so used to leave no trace at
            # all. The drawing plainly *has* a bolts layer; the pipeline
            # simply could not say so.
            cad.entities_per_layer.setdefault(layer, {})
            counts = cad.entities_per_layer[layer]
            counts[dxftype] = counts.get(dxftype, 0) + 1

            if dxftype in _TEXT_TYPES:
                self._read_text(cad, layout_index, entity, dxftype, layer, transform)
            elif dxftype == "INSERT":
                self._read_insert(cad, layout_index, entity, layer, transform, depth)
                self._read_block_contents(cad, layout_index, entity, depth, seen_blocks, transform)
            elif dxftype.startswith("DIMENSION"):
                self._read_dimension(cad, layout_index, entity, layer)
            elif dxftype in _GEOMETRY_TYPES:
                self._read_geometry(
                    cad, layout_index, entity, dxftype, layer, transform, block_name
                )

    def _read_block_contents(
        self,
        cad: CadDocument,
        layout_index: int,
        insert: Any,
        depth: int,
        seen_blocks: frozenset[str],
        transform: Transform,
    ) -> None:
        """Follow an INSERT into the block it references.

        Without this, everything a block contains is invisible. On the
        reference drawing that is 35 of 45 non-empty text entities -- 78% --
        including the entire bolt specification (`ALL BOLTS 3/4" DIA. A325`),
        the column, beam and purlin callouts, the angle and bent-plate sizes,
        and every dimension. Reading model space alone saw the disclaimer and
        the drawing number, and nothing an engineer would ask about.

        This is not exotic: a CAD user grouping annotation into a block is
        ordinary practice, and R12 exports do it automatically. `seen_blocks`
        guards against a block that references itself, which would otherwise
        recurse forever.
        """
        if depth >= self._max_block_depth:
            return
        name = str(getattr(insert.dxf, "name", ""))
        if not name or name in seen_blocks or self._blocks is None:
            return
        try:
            block = self._blocks.get(name)
        except Exception:  # pragma: no cover - malformed reference
            return
        if block is None:
            return
        self._record_definition(cad, name, block)
        self._read_space(
            cad,
            layout_index,
            block,
            depth + 1,
            seen_blocks | {name},
            _compose(transform, self._insert_transform(insert)),
            name,
        )

    def _record_definition(self, cad: CadDocument, name: str, block: Any) -> None:
        """Describe a block definition once, however often it is placed.

        Recorded here rather than by iterating the block table because the
        table also holds definitions nothing on the sheet references -- a
        template's unused symbol library -- and a schedule of blocks that do
        not appear on the drawing describes the template, not the drawing.
        """
        if name in self._seen_definitions:
            return
        self._seen_definitions.add(name)

        counts: dict[str, int] = {}
        texts: list[tuple[float, str]] = []
        nested: list[str] = []
        xs: list[float] = []
        ys: list[float] = []

        for entity in block:
            dxftype = entity.dxftype()
            counts[dxftype] = counts.get(dxftype, 0) + 1
            if dxftype == "INSERT":
                nested.append(str(getattr(entity.dxf, "name", "")))
            if dxftype in _TEXT_TYPES:
                body = self._entity_text(entity, dxftype).strip()
                insert = getattr(entity.dxf, "insert", None)
                # Sorted by descending Y so a three-line note reads top to
                # bottom, the way it does on paper. DXF stores no such order.
                if body:
                    texts.append((-float(insert[1]) if insert is not None else 0.0, body))
            for x, y in self._points_of(entity, dxftype) or self._anchor_of(entity):
                xs.append(x)
                ys.append(y)

        cad.block_definitions.append(
            BlockDefinition(
                name=name,
                is_anonymous=name.startswith("*"),
                primitive_counts=counts,
                texts=tuple(body for _, body in sorted(texts, key=lambda t: t[0])),
                nested=tuple(n for n in nested if n),
                width=round(max(xs) - min(xs), 3) if xs else 0.0,
                height=round(max(ys) - min(ys), 3) if ys else 0.0,
            )
        )

    def _anchor_of(self, entity: Any) -> list[tuple[float, float]]:
        """Insertion point, for entities `_points_of` does not locate."""
        insert = getattr(entity.dxf, "insert", None)
        if insert is None:
            return []
        try:
            return [(float(insert[0]), float(insert[1]))]
        except Exception:  # pragma: no cover - malformed insertion point
            return []

    def _insert_transform(self, insert: Any) -> Transform:
        """The block's own frame mapped into its container's.

        Block contents are stored in block-local coordinates, so without this
        a leader drawn inside a block and the geometry it points at would be
        expressed in different frames and every distance between them would be
        meaningless. Most detail exports insert at the origin unscaled, which
        is exactly why this is easy to get wrong and not notice.
        """
        import math

        try:
            point = getattr(insert.dxf, "insert", None)
            ex = float(getattr(insert.dxf, "xscale", 1.0) or 1.0)
            ey = float(getattr(insert.dxf, "yscale", 1.0) or 1.0)
            angle = math.radians(float(getattr(insert.dxf, "rotation", 0.0) or 0.0))
            tx = float(point[0]) if point is not None else 0.0
            ty = float(point[1]) if point is not None else 0.0
        except Exception:  # pragma: no cover - malformed INSERT
            return _IDENTITY
        cos, sin = math.cos(angle), math.sin(angle)
        return (ex * cos, ex * sin, -ey * sin, ey * cos, tx, ty)

    def _read_geometry(
        self,
        cad: CadDocument,
        layout_index: int,
        entity: Any,
        dxftype: str,
        layer: str,
        transform: Transform,
        block_name: str,
    ) -> None:
        points = self._points_of(entity, dxftype)
        if not points:
            return
        cad.geometry.append(
            CadGeometry(
                entity_id=str(getattr(entity.dxf, "handle", "") or ""),
                entity_type=dxftype,
                layer=layer,
                layout_index=layout_index,
                points=tuple(_apply(transform, x, y) for x, y in points),
                block_name=block_name,
            )
        )

    def _points_of(self, entity: Any, dxftype: str) -> list[tuple[float, float]]:
        """Vertices in the entity's own frame.

        Curves are sampled rather than described: an association only needs to
        know roughly where a circle's edge is, and four quadrant points answer
        that without carrying radius and angle semantics into every consumer.
        """
        import math

        try:
            if dxftype == "LINE":
                start, end = entity.dxf.start, entity.dxf.end
                return [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
            if dxftype == "LWPOLYLINE":
                return [(float(p[0]), float(p[1])) for p in entity.get_points()]
            if dxftype == "POLYLINE":
                return [
                    (float(v.dxf.location[0]), float(v.dxf.location[1])) for v in entity.vertices
                ]
            if dxftype in ("SOLID", "TRACE"):
                out = []
                for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    v = getattr(entity.dxf, name, None)
                    if v is not None:
                        out.append((float(v[0]), float(v[1])))
                return out
            if dxftype in ("CIRCLE", "ARC"):
                centre = entity.dxf.center
                cx, cy = float(centre[0]), float(centre[1])
                r = float(entity.dxf.radius)
                if dxftype == "CIRCLE":
                    angles = [0.0, 90.0, 180.0, 270.0]
                else:
                    a0 = float(entity.dxf.start_angle)
                    a1 = float(entity.dxf.end_angle)
                    if a1 < a0:
                        a1 += 360.0
                    angles = [a0, (a0 + a1) / 2, a1]
                return [
                    (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
                    for a in angles
                ]
        except Exception:  # pragma: no cover - malformed geometry
            return []
        return []

    def _read_text(
        self,
        cad: CadDocument,
        layout_index: int,
        entity: Any,
        dxftype: str,
        layer: str,
        transform: Transform = _IDENTITY,
    ) -> None:
        text = self._entity_text(entity, dxftype)
        if not text.strip():
            return
        height = float(getattr(entity.dxf, "height", 0.0) or 0.0)
        cad.texts.append(
            CadTextEntity(
                text=text,
                layer=layer,
                layout_index=layout_index,
                entity_type=dxftype,
                bbox=self._text_bbox(entity, transform),
                height=height * _scale_of(transform),
                rotation=float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
            )
        )

    def _read_insert(
        self,
        cad: CadDocument,
        layout_index: int,
        entity: Any,
        layer: str,
        transform: Transform = _IDENTITY,
        depth: int = 0,
    ) -> None:
        block_name = str(getattr(entity.dxf, "name", ""))
        attributes: dict[str, str] = {}
        for attrib in getattr(entity, "attribs", []) or []:
            tag = str(getattr(attrib.dxf, "tag", "")).strip()
            value = str(getattr(attrib.dxf, "text", "")).strip()
            if not tag or not value:
                continue
            attributes[tag] = value
            # A title block is just a block whose attributes happen to be the
            # drawing's identity. Recognised by tag rather than by block name,
            # because every template set names the block differently.
            field = normalise_tag(tag)
            if field in _TITLE_BLOCK_TAGS.values():
                cad.title_block.append(
                    TitleBlockField(
                        tag=field,
                        value=value,
                        block_name=block_name,
                        layout_index=layout_index,
                    )
                )

        insert = getattr(entity.dxf, "insert", None)
        point = (
            _apply(transform, float(insert[0]), float(insert[1]))
            if insert is not None
            else _apply(transform, 0.0, 0.0)
        )
        cad.parts.append(
            PartInstance(
                block_name=block_name,
                layer=layer,
                layout_index=layout_index,
                insert_point=point,
                attributes=attributes,
                depth=depth,
            )
        )

    def _read_dimension(self, cad: CadDocument, layout_index: int, entity: Any, layer: str) -> None:
        measurement = getattr(entity.dxf, "actual_measurement", None)
        if measurement is None:
            measurement = getattr(entity, "get_measurement", lambda: None)()
        if measurement is None:
            return

        override = str(getattr(entity.dxf, "text", "") or "").strip()
        # `<>` is AutoCAD's placeholder meaning "show the measured value", so
        # it is not an override at all.
        if override in ("<>", ""):
            override = ""

        cad.dimensions.append(
            DimensionRecord(
                measurement=float(measurement),
                dimension_type=entity.dxftype(),
                layer=layer,
                layout_index=layout_index,
                text_override=override,
            )
        )

    def _entity_text(self, entity: Any, dxftype: str) -> str:
        if dxftype == "MTEXT":
            # MTEXT embeds formatting codes (\P for a paragraph break, \f for
            # a font change). `plain_text` strips them; without it the
            # extracted string is unreadable and unsearchable.
            plain = getattr(entity, "plain_text", None)
            if callable(plain):
                return str(plain())
            return str(getattr(entity, "text", ""))
        return str(getattr(entity.dxf, "text", ""))

    def _text_bbox(self, entity: Any, transform: Transform = _IDENTITY) -> BoundingBox | None:
        """A model-space rectangle around the text.

        Approximated from the insertion point and the text height rather than
        measured: exact glyph extents need a font engine, and the purpose
        here is to locate the text on the sheet for highlighting, not to
        typeset it. `space="model"` marks the coordinate system so a consumer
        cannot mistake these for page coordinates -- CAD model space is
        Y-up and unbounded, page space is Y-down and bounded.

        The extent matters as much as the insertion point. `PURLIN, SEE PLAN`
        is inserted at x=11.5 and its leader ends at x=28.9; measured from the
        insertion point that is 16 units and looks like no relationship at
        all, but the text runs to x=25.9 so the actual gap is about 3. Reading
        only insertion points loses left-hand callouts entirely.
        """
        insert = getattr(entity.dxf, "insert", None)
        if insert is None:
            return None
        height = float(getattr(entity.dxf, "height", 0.0) or 0.0)
        if height <= 0:
            height = 2.5
        text = self._entity_text(entity, entity.dxftype())
        # Longest line, not total length: MTEXT wraps, and measuring the
        # whole string would give a paragraph the width of a sentence.
        longest = max((len(line) for line in text.splitlines()), default=1)
        width = max(longest, 1) * height * 0.6
        lines = max(text.count("\n") + 1, 1)

        x0, y0 = float(insert[0]), float(insert[1])
        corners = [
            _apply(transform, x0, y0),
            _apply(transform, x0 + width, y0),
            _apply(transform, x0, y0 + height * lines),
            _apply(transform, x0 + width, y0 + height * lines),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        return BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys), space="model")
