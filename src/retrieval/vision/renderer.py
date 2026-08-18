"""Turning one region of one drawing into one small PNG.

Native CAD is never rasterised as a matter of course -- the whole point of
the earlier phases is that a DXF is read exactly, and a raster of it is
strictly worse. Rendering happens here, once, only for a region the
deterministic pipeline has already said it cannot name.

Two decisions worth stating:

**The crop is padded.** A symbol cropped to its own bounding box is a shape
with nothing around it, and what a symbol means is usually carried by what it
sits on -- an arrowhead on a leader, a hatch inside a member outline. The
padding is a fraction of the region, so it scales with what is being looked
at rather than being a fixed number of units that is huge on a detail and
invisible on a general arrangement.

**Output is capped.** A drawing rendered at native resolution is a large
image, and image tokens are the expensive part of a vision call. The cap is
on the longest edge, applied after rendering, so a wide crop and a tall one
both come out bounded.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Protocol

from src.domain.value_objects.provenance import Region
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class RegionRenderer(Protocol):
    """Renders a normalised region of a document page to PNG bytes."""

    def render(
        self, source: Path, page_number: int, region: Region, max_pixels: int
    ) -> bytes | None: ...


def _padded(region: Region, padding: float) -> tuple[float, float, float, float]:
    """The region grown by a fraction of itself, clamped to the sheet."""
    pad_x = max(region.width, 0.01) * padding
    pad_y = max(region.height, 0.01) * padding
    return (
        max(0.0, region.x0 - pad_x),
        max(0.0, region.y0 - pad_y),
        min(1.0, region.x1 + pad_x),
        min(1.0, region.y1 + pad_y),
    )


class CadRegionRenderer:
    """Renders a DXF region with ezdxf's own drawing add-on.

    Uses the same library that read the file, so what the model sees is what
    the reader parsed -- a separate renderer could disagree with the
    extraction, and a disagreement between our own two views of one file is
    the least useful kind of uncertainty to introduce.
    """

    def __init__(self, padding: float = 0.35, dpi: int = 150) -> None:
        self._padding = padding
        self._dpi = dpi

    def render(
        self, source: Path, page_number: int, region: Region, max_pixels: int = 1024
    ) -> bytes | None:
        try:
            import ezdxf
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            # ezdxf's add-on is a documented entry point; its package just
            # does not declare explicit re-exports for the type checker.
            from ezdxf.addons.drawing import (  # type: ignore[attr-defined]
                Frontend,
                RenderContext,
            )
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        except Exception as exc:  # pragma: no cover - optional render stack
            logger.info("vision_render_unavailable", error=str(exc))
            return None

        try:
            doc = ezdxf.readfile(str(source))  # type: ignore[attr-defined]
            msp = doc.modelspace()
            extents = _model_extents(doc, msp)
            if extents is None:
                return None
            x0, y0, x1, y1 = extents

            # The region arrives in sheet space (0..1, Y-down). Model space is
            # Y-up, so the flip has to be undone to find the same rectangle in
            # the drawing's own coordinates.
            px0, py0, px1, py1 = _padded(region, self._padding)
            width, height = x1 - x0, y1 - y0
            mx0 = x0 + px0 * width
            mx1 = x0 + px1 * width
            my1 = y0 + (1.0 - py0) * height
            my0 = y0 + (1.0 - py1) * height

            figure = plt.figure(dpi=self._dpi)
            axes = figure.add_axes((0.0, 0.0, 1.0, 1.0))
            axes.set_axis_off()
            Frontend(RenderContext(doc), MatplotlibBackend(axes)).draw_layout(msp, finalize=False)
            axes.set_xlim(mx0, mx1)
            axes.set_ylim(my0, my1)

            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=self._dpi, bbox_inches="tight")
            plt.close(figure)
            return _capped(buffer.getvalue(), max_pixels)
        except Exception as exc:
            logger.warning("vision_render_failed", source=str(source), error=str(exc))
            return None


class PdfRegionRenderer:
    """Renders a PDF page region, for scanned and plotted drawings.

    The scanned path needs this for the same reason the CAD path does: OCR
    gives words and boxes, and a symbol is neither.
    """

    def __init__(self, padding: float = 0.35, scale: float = 3.0) -> None:
        self._padding = padding
        self._scale = scale

    def render(
        self, source: Path, page_number: int, region: Region, max_pixels: int = 1024
    ) -> bytes | None:
        try:
            import pypdfium2
            from PIL import Image
        except Exception as exc:  # pragma: no cover - optional render stack
            logger.info("vision_render_unavailable", error=str(exc))
            return None

        try:
            pdf = pypdfium2.PdfDocument(str(source))
            index = max(0, (page_number or 1) - 1)
            if index >= len(pdf):
                return None
            rendered = pdf[index].render(scale=self._scale).to_pil()
            px0, py0, px1, py1 = _padded(region, self._padding)
            box = (
                int(px0 * rendered.width),
                int(py0 * rendered.height),
                max(int(px1 * rendered.width), int(px0 * rendered.width) + 1),
                max(int(py1 * rendered.height), int(py0 * rendered.height) + 1),
            )
            crop: Image.Image = rendered.crop(box)
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG")
            return _capped(buffer.getvalue(), max_pixels)
        except Exception as exc:
            logger.warning("vision_render_failed", source=str(source), error=str(exc))
            return None


def _model_extents(doc: object, msp: object) -> tuple[float, float, float, float] | None:
    """The same extents the loader normalised against.

    Recomputed rather than stored because the renderer is handed a file and a
    rectangle, not a `CadDocument` -- but it must agree with
    `dxf_loader._SheetFrame`, or the crop lands somewhere else on the sheet.
    """
    try:
        header = doc.header  # type: ignore[attr-defined]
        low, high = header.get("$EXTMIN"), header.get("$EXTMAX")
        xs: list[float] = []
        ys: list[float] = []
        if low is not None and high is not None:
            xs += [float(low[0]), float(high[0])]
            ys += [float(low[1]), float(high[1])]
        for entity in msp:  # type: ignore[attr-defined]
            point = getattr(entity.dxf, "insert", None) or getattr(entity.dxf, "start", None)
            if point is not None:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
        if not xs or not ys or max(xs) <= min(xs) or max(ys) <= min(ys):
            return None
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:  # pragma: no cover - malformed drawing
        return None


def _capped(png: bytes, max_pixels: int) -> bytes:
    """Shrink so the longest edge fits, leaving smaller images alone."""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return png
    try:
        image = Image.open(io.BytesIO(png))
        longest = max(image.width, image.height)
        if longest <= max_pixels:
            return png
        ratio = max_pixels / longest
        resized = image.resize(
            (max(1, math.floor(image.width * ratio)), max(1, math.floor(image.height * ratio)))
        )
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # pragma: no cover
        return png


def renderer_for(file_type: str) -> RegionRenderer | None:
    lowered = (file_type or "").lower().lstrip(".")
    if lowered in ("dxf", "dwg"):
        return CadRegionRenderer()
    if lowered == "pdf":
        return PdfRegionRenderer()
    return None
