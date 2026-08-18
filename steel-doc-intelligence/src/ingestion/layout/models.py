from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Heading:
    text: str
    level: int
    page_number: int | None = None


@dataclass
class TableRef:
    caption: str = ""
    page_number: int | None = None
    row_count: int = 0
    col_count: int = 0


@dataclass
class FigureRef:
    caption: str = ""
    page_number: int | None = None


@dataclass
class Caption:
    text: str
    page_number: int | None = None


@dataclass
class ListBlock:
    items: list[str] = field(default_factory=list)
    page_number: int | None = None


@dataclass
class FormField:
    key: str
    value: str = ""
    page_number: int | None = None


@dataclass
class Footnote:
    text: str
    page_number: int | None = None


@dataclass
class OutlineNode:
    title: str
    level: int
    page_number: int | None = None
    children: list[OutlineNode] = field(default_factory=list)


@dataclass
class DocumentLayout:
    """Structured document layout -- headings/tables/figures/captions/lists/
    forms/footnotes/outline kept as distinct typed collections rather than
    flattened into plain text (Part 3's explicit requirement)."""

    headings: list[Heading] = field(default_factory=list)
    tables: list[TableRef] = field(default_factory=list)
    figures: list[FigureRef] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    lists: list[ListBlock] = field(default_factory=list)
    forms: list[FormField] = field(default_factory=list)
    footnotes: list[Footnote] = field(default_factory=list)
    outline: list[OutlineNode] = field(default_factory=list)
