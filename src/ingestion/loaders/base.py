from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class TextBlock:
    text: str
    page_number: int | None = None
    section: str | None = None
    # Structural label attached by the loader when it has one available
    # (Docling: DocItemLabel value e.g. "section_header"/"list_item"/
    # "caption"/"footnote"/"title"; Unstructured: element.category e.g.
    # "Title"/"ListItem"/"Table"). None when the loader has no structural
    # signal (e.g. a plain .txt read) -- LayoutAnalyzer falls back to
    # heuristics in that case. Additive field: existing construction call
    # sites and tests are unaffected by the default.
    element_label: str | None = None
    heading_level: int | None = None
    is_footnote: bool = False
    bbox: BoundingBox | None = None
    # Mean per-word OCR confidence for blocks produced by an OCR provider,
    # None for blocks that came from a native text layer. Carried per block
    # rather than per document so ChunkValidator can judge each chunk on its
    # own legibility -- a drawing with one illegible stamp should not lose
    # its legible schedule.
    ocr_confidence: float | None = None


@dataclass
class TableBlock:
    markdown: str
    page_number: int | None = None
    caption: str = ""
    row_count: int = 0
    col_count: int = 0
    bbox: BoundingBox | None = None


@dataclass
class ImageRef:
    caption: str = ""
    page_number: int | None = None
    alt_text: str = ""


@dataclass
class RawDocument:
    file_path: Path
    file_name: str
    mime_type: str
    text_blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    image_refs: list[ImageRef] = field(default_factory=list)
    page_count: int | None = None
    word_count: int | None = None
    loader_name: str = ""

    @property
    def full_text(self) -> str:
        parts = [block.text for block in self.text_blocks if block.text.strip()]
        table_parts = [f"\n\n{t.markdown}\n\n" for t in self.tables]
        return "\n\n".join(parts) + "".join(table_parts)


class DocumentLoader(ABC):
    """Abstract base for all document loaders."""

    @abstractmethod
    def supports(self, mime_type: str, file_extension: str) -> bool:
        ...

    @abstractmethod
    async def load(self, file_path: Path) -> RawDocument:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
