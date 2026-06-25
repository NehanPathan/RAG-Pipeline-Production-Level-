from __future__ import annotations

from pathlib import Path

from src.ingestion.loaders.base import DocumentLoader, RawDocument, TableBlock, TextBlock
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MIMES = {
    "text/plain", "text/markdown", "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SUPPORTED_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".pdf", ".docx"}


class UnstructuredLoader(DocumentLoader):
    """Fallback loader using Unstructured — handles HTML, TXT, MD, and more."""

    @property
    def name(self) -> str:
        return "unstructured"

    def supports(self, mime_type: str, file_extension: str) -> bool:
        return mime_type in SUPPORTED_MIMES or file_extension.lower() in SUPPORTED_EXTENSIONS

    async def load(self, file_path: Path) -> RawDocument:
        from unstructured.partition.auto import partition

        logger.info("unstructured_load_start", file=str(file_path))

        elements = partition(filename=str(file_path))

        text_blocks: list[TextBlock] = []
        tables: list[TableBlock] = []
        current_page = 1

        for element in elements:
            element_type = type(element).__name__
            page_number = element.metadata.page_number if hasattr(element.metadata, "page_number") else current_page

            if element_type == "Table":
                tables.append(
                    TableBlock(
                        markdown=str(element),
                        page_number=page_number,
                    )
                )
            else:
                text = str(element).strip()
                if text:
                    text_blocks.append(
                        TextBlock(
                            text=text,
                            page_number=page_number,
                            section=element.metadata.section if hasattr(element.metadata, "section") else None,
                        )
                    )

        raw = RawDocument(
            file_path=file_path,
            file_name=file_path.name,
            mime_type=self._guess_mime(file_path),
            text_blocks=text_blocks,
            tables=tables,
            loader_name=self.name,
        )
        raw.word_count = len(raw.full_text.split())
        logger.info("unstructured_load_done", file=str(file_path), elements=len(elements))
        return raw

    def _guess_mime(self, file_path: Path) -> str:
        ext_map = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".html": "text/html",
            ".htm": "text/html",
        }
        return ext_map.get(file_path.suffix.lower(), "application/octet-stream")
