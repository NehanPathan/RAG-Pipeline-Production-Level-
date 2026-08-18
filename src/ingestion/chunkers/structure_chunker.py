from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.value_objects.provenance import Region
from src.ingestion.loaders.base import BoundingBox
from src.ingestion.parsing.parsed_document import ParsedDocument


@dataclass
class StructuralSection:
    text: str
    page_number: int | None = None
    section_title: str | None = None
    heading_level: int | None = None
    is_table: bool = False
    # Mean OCR confidence of the blocks that contributed to this section,
    # None when none of them came from OCR. Carried so ChunkValidator can
    # judge each chunk on its own legibility rather than on the document
    # average -- see ChunkValidator.validate.
    ocr_confidence: float | None = None
    # Boxes of the blocks that contributed, so a chunk cut from this section
    # can say where on the page it came from. Accumulated in parallel with
    # `text` -- the two are flushed together, which is what keeps them
    # describing the same content.
    regions: list[Region] = field(default_factory=list)
    # CAD layers the contributing blocks came from, in first-seen
    # order. Empty for prose.
    layers: list[str] = field(default_factory=list)


def _region_of(bbox: BoundingBox | None, page_number: int | None) -> Region | None:
    """A block's box as a region, or None when it has neither box nor page.

    Blocks without a box are the common case on prose loaders, and a region
    invented for them would be a rectangle around nothing.
    """
    if bbox is None or page_number is None:
        return None
    return Region(
        page_number=page_number,
        x0=bbox.x0,
        y0=bbox.y0,
        x1=bbox.x1,
        y1=bbox.y1,
        space=bbox.space,
    )


def _mean_confidence(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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
        buffer_confidences: list[float] = []
        buffer_regions: list[Region] = []
        buffer_layers: list[str] = []
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
            nonlocal buffer, buffer_page, buffer_confidences, buffer_regions
            nonlocal buffer_layers
            flush_list()
            text = "\n\n".join(part for part in buffer if part.strip())
            if text.strip():
                sections.append(
                    StructuralSection(
                        text=text,
                        page_number=buffer_page,
                        section_title=current_heading_text,
                        heading_level=current_heading_level,
                        ocr_confidence=_mean_confidence(buffer_confidences),
                        regions=Region.merge(buffer_regions),
                        layers=list(buffer_layers),
                    )
                )
            buffer = []
            buffer_page = None
            buffer_confidences = []
            buffer_regions = []
            buffer_layers = []

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

            # A page boundary ends a section, even mid-heading. A section carries one
            # page number, so one spanning five pages claims to be on the first -- and
            # every chunk cut from it inherits that claim, which is what a citation
            # quotes. Found by a five-page fixture whose only heading was on page 5:
            # pages 3 and 4 produced no separately-citable chunk at all. Splitting a
            # paragraph across a break is the right trade; the heading carries over.
            if (
                buffer_page is not None
                and block.page_number is not None
                and block.page_number != buffer_page
            ):
                flush_section()

            if block.ocr_confidence is not None:
                buffer_confidences.append(block.ocr_confidence)
            region = _region_of(block.bbox, block.page_number)
            if region is not None:
                buffer_regions.append(region)
            for layer in block.layers:
                if layer not in buffer_layers:
                    buffer_layers.append(layer)

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
                    regions=Region.merge(
                        [r for r in [_region_of(table.bbox, table.page_number)] if r]
                    ),
                    layers=list(table.layers),
                )
            )

        return sections
