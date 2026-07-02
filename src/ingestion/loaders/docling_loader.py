from __future__ import annotations

from pathlib import Path

from src.ingestion.loaders.base import DocumentLoader, ImageRef, RawDocument, TableBlock, TextBlock
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
        from docling.document_converter import DocumentConverter

        logger.info("docling_load_start", file=str(file_path))

        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        doc = result.document

        text_blocks: list[TextBlock] = []
        tables: list[TableBlock] = []
        image_refs: list[ImageRef] = []

        # Extract text by page. `doc.pages` is keyed by page number and holds
        # only page geometry (size/image) — the actual text content lives in
        # the flat `doc.texts` list, with each item's page number coming from
        # its first provenance entry.
        page_text_parts: dict[int, list[str]] = {page_no: [] for page_no in doc.pages}
        for item in doc.texts:
            if not item.text or not item.prov:
                continue
            page_text_parts.setdefault(item.prov[0].page_no, []).append(item.text)

        for page_no in sorted(page_text_parts):
            parts = page_text_parts[page_no]
            if parts:
                text_blocks.append(
                    TextBlock(
                        text="\n".join(parts),
                        page_number=page_no,
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
