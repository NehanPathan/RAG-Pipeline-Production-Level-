from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.parsing.parsed_document import ParsedDocument


@dataclass
class StructuralSection:
    text: str
    page_number: int | None = None
    section_title: str | None = None
    heading_level: int | None = None
    is_table: bool = False


class StructureChunker:
    """Part 4, Step 1: splits a ParsedDocument along its document structure
    -- headings, paragraphs, tables, lists -- before any semantic or
    token-based splitting happens.

    Walks `parsed_document.raw.text_blocks` directly (each block already
    carries `element_label`/`heading_level` from DoclingLoader/
    UnstructuredLoader, Gap 1) rather than `parsed_document.layout`, since
    layout's per-type collections (headings/lists/...) have already lost
    original document order relative to plain paragraphs -- the block
    stream is what preserves "this paragraph belongs under this heading."
    Consecutive list items are merged into one bullet section; tables come
    from `raw.tables` (which has actual markdown content, unlike
    `layout.tables`, which is index/reference-only for the UI outline).
    """

    def split(self, parsed_document: ParsedDocument) -> list[StructuralSection]:
        sections: list[StructuralSection] = []
        current_heading_text: str | None = None
        current_heading_level: int | None = None
        buffer: list[str] = []
        buffer_page: int | None = None
        list_buffer: list[str] = []
        list_page: int | None = None

        def flush_list() -> None:
            nonlocal list_buffer, list_page, buffer_page
            if list_buffer:
                buffer.append("\n".join(f"- {item}" for item in list_buffer))
                if buffer_page is None:
                    buffer_page = list_page
            list_buffer = []
            list_page = None

        def flush_section() -> None:
            nonlocal buffer, buffer_page
            flush_list()
            text = "\n\n".join(part for part in buffer if part.strip())
            if text.strip():
                sections.append(
                    StructuralSection(
                        text=text,
                        page_number=buffer_page,
                        section_title=current_heading_text,
                        heading_level=current_heading_level,
                    )
                )
            buffer = []
            buffer_page = None

        for block in parsed_document.raw.text_blocks:
            text = block.text.strip()
            if not text:
                continue
            label = block.element_label

            if label in ("section_header", "title"):
                flush_section()
                current_heading_text = text
                current_heading_level = (
                    block.heading_level
                    if block.heading_level is not None
                    else (0 if label == "title" else 1)
                )
                continue

            if label == "list_item":
                list_buffer.append(text)
                list_page = list_page if list_page is not None else block.page_number
                continue
            flush_list()

            buffer.append(text)
            if buffer_page is None:
                buffer_page = block.page_number

        flush_section()

        for table in parsed_document.raw.tables:
            if not table.markdown.strip():
                continue
            sections.append(
                StructuralSection(
                    text=table.markdown,
                    page_number=table.page_number,
                    section_title=table.caption or None,
                    is_table=True,
                )
            )

        return sections
