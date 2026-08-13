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
    CadDocument,
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

    def read(self, file_path: Path) -> CadDocument:
        """Parse a DXF. Synchronous and CPU-bound; the loader runs it in a thread."""
        import ezdxf

        # `readfile` is ezdxf's documented entry point; its package just does
        # not declare an explicit re-export for the type checker to follow.
        doc = ezdxf.readfile(str(file_path))  # type: ignore[attr-defined]
        # Block definitions are needed to follow INSERTs into their contents.
        self._blocks = doc.blocks
        cad = CadDocument(
            layers=sorted(layer.dxf.name for layer in doc.layers),
            insunits=str(doc.header.get("$INSUNITS", "")),
        )

        # Model space is page 1, then each paper-space layout in order. An
        # engineer refers to sheets, and this is the mapping that makes a
        # citation say "sheet 2" and mean it.
        spaces: list[tuple[LayoutSheet, Any]] = []
        model = doc.modelspace()
        spaces.append((LayoutSheet(name="Model", index=0, is_model_space=True), model))
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
            proxy_ratio=round(cad.proxy_ratio, 3),
        )
        return cad

    def _read_space(
        self,
        cad: CadDocument,
        layout_index: int,
        space: Any,
        depth: int = 0,
        seen_blocks: frozenset[str] = frozenset(),
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
                self._read_text(cad, layout_index, entity, dxftype, layer)
            elif dxftype == "INSERT":
                self._read_insert(cad, layout_index, entity, layer)
                self._read_block_contents(cad, layout_index, entity, depth, seen_blocks)
            elif dxftype.startswith("DIMENSION"):
                self._read_dimension(cad, layout_index, entity, layer)

    def _read_block_contents(
        self,
        cad: CadDocument,
        layout_index: int,
        insert: Any,
        depth: int,
        seen_blocks: frozenset[str],
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
        self._read_space(cad, layout_index, block, depth + 1, seen_blocks | {name})

    def _read_text(
        self, cad: CadDocument, layout_index: int, entity: Any, dxftype: str, layer: str
    ) -> None:
        text = self._entity_text(entity, dxftype)
        if not text.strip():
            return
        cad.texts.append(
            CadTextEntity(
                text=text,
                layer=layer,
                layout_index=layout_index,
                entity_type=dxftype,
                bbox=self._text_bbox(entity),
                height=float(getattr(entity.dxf, "height", 0.0) or 0.0),
                rotation=float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
            )
        )

    def _read_insert(self, cad: CadDocument, layout_index: int, entity: Any, layer: str) -> None:
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
        point = (float(insert[0]), float(insert[1])) if insert is not None else (0.0, 0.0)
        cad.parts.append(
            PartInstance(
                block_name=block_name,
                layer=layer,
                layout_index=layout_index,
                insert_point=point,
                attributes=attributes,
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

    def _text_bbox(self, entity: Any) -> BoundingBox | None:
        """A model-space rectangle around the text.

        Approximated from the insertion point and the text height rather than
        measured: exact glyph extents need a font engine, and the purpose
        here is to locate the text on the sheet for highlighting, not to
        typeset it. `space="model"` marks the coordinate system so a consumer
        cannot mistake these for page coordinates -- CAD model space is
        Y-up and unbounded, page space is Y-down and bounded.
        """
        insert = getattr(entity.dxf, "insert", None)
        if insert is None:
            return None
        height = float(getattr(entity.dxf, "height", 0.0) or 0.0)
        if height <= 0:
            height = 2.5
        text = self._entity_text(entity, entity.dxftype())
        width = max(len(text), 1) * height * 0.6

        x0, y0 = float(insert[0]), float(insert[1])
        return BoundingBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + height, space="model")
