from __future__ import annotations

import re

from src.ingestion.layout.base import LayoutAnalyzer
from src.ingestion.layout.models import DocumentLayout, Heading, ListBlock, OutlineNode, TableRef
from src.ingestion.loaders.base import RawDocument

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MAX_HEADING_WORDS = 12


class HeuristicLayoutAnalyzer(LayoutAnalyzer):
    """Fallback structure extraction for RawDocuments with no
    `element_label` metadata at all (e.g. a plain .txt with no structural
    signal). Regex/shape heuristics only -- materially lower fidelity than
    LabeledLayoutAnalyzer, and unable to detect forms/footnotes at all since
    those have no reliable textual signal once flattened. Selected by
    DocumentParsingService only when every text block is unlabeled.
    """

    def analyze(self, raw_document: RawDocument) -> DocumentLayout:
        layout = DocumentLayout()
        list_items: list[str] = []
        list_page: int | None = None

        def flush_list() -> None:
            nonlocal list_items, list_page
            if list_items:
                layout.lists.append(ListBlock(items=list(list_items), page_number=list_page))
            list_items = []
            list_page = None

        for block in raw_document.text_blocks:
            line = block.text.strip()
            if not line:
                continue

            if _LIST_ITEM_RE.match(line):
                list_items.append(_LIST_ITEM_RE.sub("", line))
                list_page = list_page or block.page_number
                continue
            flush_list()

            if self._looks_like_heading(line):
                layout.headings.append(Heading(text=line, level=1, page_number=block.page_number))

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

        layout.outline = [
            OutlineNode(title=h.text, level=h.level, page_number=h.page_number) for h in layout.headings
        ]
        return layout

    def _looks_like_heading(self, line: str) -> bool:
        if len(line) > 80 or line.endswith((".", ",", ";", ":")):
            return False
        word_count = len(line.split())
        if word_count == 0 or word_count > _MAX_HEADING_WORDS:
            return False
        return line[:1].isupper() or line.isupper()
