from __future__ import annotations

from src.ingestion.layout.base import LayoutAnalyzer
from src.ingestion.layout.models import (
    Caption,
    DocumentLayout,
    FigureRef,
    Footnote,
    FormField,
    Heading,
    ListBlock,
    OutlineNode,
    TableRef,
)
from src.ingestion.loaders.base import RawDocument


class LabeledLayoutAnalyzer(LayoutAnalyzer):
    """Builds DocumentLayout from `TextBlock.element_label` metadata already
    populated by DoclingLoader/UnstructuredLoader -- the high-fidelity path,
    used whenever a loader has attached real structural labels (Gap 1 in
    docs/architecture/12_phase4a_design_review.md). Falls back to
    HeuristicLayoutAnalyzer when no text block carries a label at all.
    """

    def analyze(self, raw_document: RawDocument) -> DocumentLayout:
        layout = DocumentLayout()
        current_list_items: list[str] = []
        current_list_page: int | None = None
        pending_form_key: str | None = None

        def flush_list() -> None:
            nonlocal current_list_items, current_list_page
            if current_list_items:
                layout.lists.append(
                    ListBlock(items=list(current_list_items), page_number=current_list_page)
                )
            current_list_items = []
            current_list_page = None

        for block in raw_document.text_blocks:
            label = block.element_label
            text = block.text.strip()
            if not text:
                continue

            if label == "list_item":
                current_list_items.append(text)
                current_list_page = current_list_page or block.page_number
                continue
            flush_list()

            if label in ("section_header", "title"):
                level = block.heading_level if block.heading_level is not None else (
                    0 if label == "title" else 1
                )
                layout.headings.append(Heading(text=text, level=level, page_number=block.page_number))
            elif label == "caption":
                layout.captions.append(Caption(text=text, page_number=block.page_number))
            elif label == "footnote" or block.is_footnote:
                layout.footnotes.append(Footnote(text=text, page_number=block.page_number))
            elif label == "field_key":
                pending_form_key = text
            elif label == "field_value" and pending_form_key is not None:
                layout.forms.append(
                    FormField(key=pending_form_key, value=text, page_number=block.page_number)
                )
                pending_form_key = None

        flush_list()

        for table in raw_document.tables:
            layout.tables.append(
                TableRef(
                    caption=table.caption,
                    page_number=table.page_number,
                    row_count=table.row_count,
                    col_count=table.col_count,
                )
            )

        for image in raw_document.image_refs:
            layout.figures.append(FigureRef(caption=image.caption, page_number=image.page_number))

        layout.outline = self._build_outline(layout.headings)
        return layout

    def _build_outline(self, headings: list[Heading]) -> list[OutlineNode]:
        """Nests a flat heading list into a tree by level: each heading
        becomes the child of the most recent heading with a strictly lower
        level (stack-based), or a top-level node if none exists."""
        root: list[OutlineNode] = []
        stack: list[OutlineNode] = []

        for heading in headings:
            node = OutlineNode(title=heading.text, level=heading.level, page_number=heading.page_number)
            while stack and stack[-1].level >= node.level:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                root.append(node)
            stack.append(node)

        return root
