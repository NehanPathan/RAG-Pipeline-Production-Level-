from __future__ import annotations

from src.config import Settings
from src.ingestion.cad.dwg_converter import DwgConverter, NullDwgConverter, OdaFileConverter
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


def get_dwg_converter(settings: Settings) -> DwgConverter:
    """Select the configured DWG converter, falling back to an explicit null.

    Mirrors `ocr/registry.py`. The fallback is a real object rather than
    `None` so the loader has one code path, and so an unconfigured deployment
    produces an error naming the remedy rather than a parse failure inside a
    CAD library.
    """
    choice = (settings.cad_dwg_converter or "none").strip().lower()

    if choice == "oda":
        converter = OdaFileConverter(
            executable=settings.cad_oda_converter_path,
            timeout_seconds=settings.cad_dwg_conversion_timeout_seconds,
        )
        if not converter.available():
            # Not an error: DXF still works, and a deployment that never
            # receives DWG should not fail to start over it.
            logger.warning(
                "dwg_converter_configured_but_missing",
                path=settings.cad_oda_converter_path,
                effect="DWG uploads will be rejected with an actionable message",
            )
        return converter

    if choice not in ("none", ""):
        logger.warning("dwg_converter_unknown", configured=choice)
    return NullDwgConverter()
