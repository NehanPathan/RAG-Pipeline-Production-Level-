from __future__ import annotations

from pathlib import Path

from src.ingestion.loaders.base import DocumentLoader, RawDocument
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MIMES = {
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
}
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


class ImagePassthroughLoader(DocumentLoader):
    """Loader for standalone image files (Gap 6,
    docs/architecture/12_phase4a_design_review.md): images have no native
    text layer, so this returns an empty-text RawDocument rather than
    attempting extraction. `OCRDetector` treats any RawDocument from this
    loader as always-OCR-required (image extension check), so the OCR stage
    becomes the actual source of text -- the same way DoclingLoader/
    UnstructuredLoader's output is the source of truth for PDFs/DOCX.
    """

    @property
    def name(self) -> str:
        return "image_passthrough"

    def supports(self, mime_type: str, file_extension: str) -> bool:
        return mime_type in SUPPORTED_MIMES or file_extension.lower() in SUPPORTED_EXTENSIONS

    async def load(self, file_path: Path) -> RawDocument:
        logger.info("image_passthrough_load", file=str(file_path))
        return RawDocument(
            file_path=file_path,
            file_name=file_path.name,
            mime_type=_guess_mime(file_path),
            text_blocks=[],
            page_count=1,
            word_count=0,
            loader_name=self.name,
        )


def _guess_mime(file_path: Path) -> str:
    ext_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
    }
    return ext_map.get(file_path.suffix.lower(), "application/octet-stream")
