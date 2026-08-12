from __future__ import annotations

import asyncio
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

# Normalizes Unstructured's own element categories to the same lowercase
# snake_case vocabulary Docling's DocItemLabel uses (see DoclingLoader), so
# LayoutAnalyzer can read `TextBlock.element_label` without caring which
# loader produced it. Unstructured has no distinct "footnote" category --
# a real fidelity gap vs. Docling, documented in
# docs/architecture/12_phase4a_design_review.md.
_LABEL_MAP = {
    "Title": "title",
    "NarrativeText": "text",
    "ListItem": "list_item",
    "FigureCaption": "caption",
    "Header": "page_header",
    "Footer": "page_footer",
    "UncategorizedText": "text",
}


class UnstructuredLoader(DocumentLoader):
    """Fallback loader using Unstructured — handles HTML, TXT, MD, and more."""

    @property
    def name(self) -> str:
        return "unstructured"

    def supports(self, mime_type: str, file_extension: str) -> bool:
        return mime_type in SUPPORTED_MIMES or file_extension.lower() in SUPPORTED_EXTENSIONS

    async def load(self, file_path: Path) -> RawDocument:
        # `partition()` is synchronous and CPU-bound (for some formats it
        # shells out to `pandoc` too) -- run via `asyncio.to_thread` so it
        # doesn't block the event loop for its entire duration, same reasoning
        # as DoclingLoader (see its `load()` docstring / Phase 4A verification).
        return await asyncio.to_thread(self._parse, file_path)

    def _parse(self, file_path: Path) -> RawDocument:
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
                    # element.category is Unstructured's own label ("Title",
                    # "NarrativeText", "ListItem", "Header", "FigureCaption",
                    # etc); category_depth gives heading nesting depth for
                    # Title elements. Unstructured has no distinct "footnote"
                    # category, so is_footnote is left False here -- a real
                    # fidelity gap vs. Docling's label set, documented in
                    # docs/architecture/12_phase4a_design_review.md.
                    text_blocks.append(
                        TextBlock(
                            text=text,
                            page_number=page_number,
                            section=element.metadata.section if hasattr(element.metadata, "section") else None,
                            element_label=_LABEL_MAP.get(element_type, element_type),
                            heading_level=(
                                getattr(element.metadata, "category_depth", None)
                                if element_type == "Title"
                                else None
                            ),
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
