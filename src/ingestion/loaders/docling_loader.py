from __future__ import annotations

import asyncio
from pathlib import Path

from src.ingestion.loaders.base import (
    BoundingBox,
    DocumentLoader,
    ImageRef,
    RawDocument,
    TableBlock,
    TextBlock,
)
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}


class DoclingLoader(DocumentLoader):
    """Loader using Docling for PDF and DOCX — best table extraction quality."""

    @property
    def name(self) -> str:
        return "docling"

    def supports(self, mime_type: str, file_extension: str) -> bool:
        return mime_type in SUPPORTED_MIMES or file_extension.lower() in SUPPORTED_EXTENSIONS

    async def load(self, file_path: Path) -> RawDocument:
        # `converter.convert()` is a synchronous, CPU-bound call (Docling's
        # own layout/table/OCR model inference, often 30-90+ seconds for a
        # real PDF) -- run via `asyncio.to_thread` so it doesn't block the
        # event loop for its entire duration. Previously ran directly on the
        # event loop; with only one uvicorn worker (see docker/api.Dockerfile),
        # that froze the *entire* API -- including unrelated health checks
        # and other requests -- for as long as this took. Found during
        # Phase 4A verification (docs/architecture/12_phase4a_design_review.md).
        return await asyncio.to_thread(self._parse, file_path)

    def _parse(self, file_path: Path) -> RawDocument:
        from docling.document_converter import DocumentConverter

        logger.info("docling_load_start", file=str(file_path))

        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        doc = result.document

        text_blocks: list[TextBlock] = []
        tables: list[TableBlock] = []
        image_refs: list[ImageRef] = []

        # One TextBlock per Docling text item (not merged per page) so each
        # item's structural label ("section_header"/"list_item"/"caption"/
        # "footnote"/"title"/etc, from docling_core's DocItemLabel) and, for
        # headings, its level survive into the pipeline instead of being
        # discarded -- this is what LayoutAnalyzer (src/ingestion/layout/)
        # reads to build real document structure (see Gap 1,
        # docs/architecture/12_phase4a_design_review.md). `doc.pages` is
        # keyed by page number and holds only page geometry (size/image);
        # the text content itself lives in the flat `doc.texts` list, with
        # each item's page number coming from its first provenance entry.
        for item in doc.texts:
            if not item.text or not item.prov:
                continue
            prov = item.prov[0]
            label = item.label.value if hasattr(item.label, "value") else str(item.label)
            text_blocks.append(
                TextBlock(
                    text=item.text,
                    page_number=prov.page_no,
                    element_label=label,
                    heading_level=getattr(item, "level", None),
                    is_footnote=(label == "footnote"),
                    bbox=BoundingBox(
                        x0=prov.bbox.l, y0=prov.bbox.t, x1=prov.bbox.r, y1=prov.bbox.b
                    )
                    if getattr(prov, "bbox", None)
                    else None,
                )
            )

        # Extract tables
        for table in doc.tables:
            rows = table.data.grid if hasattr(table, "data") else []
            markdown_rows = []
            for row in rows:
                cells = [str(cell.text) if hasattr(cell, "text") else "" for cell in row]
                markdown_rows.append("| " + " | ".join(cells) + " |")

            markdown = "\n".join(markdown_rows)
            if markdown_rows:
                header_sep = "| " + " | ".join(["---"] * len(rows[0])) + " |" if rows else ""
                markdown = markdown_rows[0] + "\n" + header_sep + "\n" + "\n".join(markdown_rows[1:])

            tables.append(
                TableBlock(
                    markdown=markdown,
                    page_number=getattr(table, "page_no", None),
                    row_count=len(rows),
                    col_count=len(rows[0]) if rows else 0,
                )
            )

        # Extract image references
        for picture in doc.pictures:
            caption = getattr(picture, "caption", "")
            image_refs.append(ImageRef(caption=str(caption) if caption else ""))

        raw = RawDocument(
            file_path=file_path,
            file_name=file_path.name,
            mime_type="application/pdf" if file_path.suffix.lower() == ".pdf" else
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text_blocks=text_blocks,
            tables=tables,
            image_refs=image_refs,
            page_count=len(doc.pages),
            loader_name=self.name,
        )
        raw.word_count = len(raw.full_text.split())
        logger.info("docling_load_done", file=str(file_path), pages=raw.page_count, tables=len(tables))
        return raw
