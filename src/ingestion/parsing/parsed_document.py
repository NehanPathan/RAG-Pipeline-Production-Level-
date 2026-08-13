from __future__ import annotations

from dataclasses import dataclass, field

from src.ingestion.layout.models import DocumentLayout
from src.ingestion.loaders.base import RawDocument
from src.ingestion.ocr.models import OCRMetadata
from src.ingestion.parsing.drawing_detector import ContentClassification, ContentKind


@dataclass
class ParsedDocument:
    """Unified output of Load + OCR (if required) + Layout analysis -- the
    one object HybridChunkingPipeline and the LLM enricher consume from this
    point on, regardless of which loader/OCR engine/layout analyzer produced
    it (Part 1's "one unified ParsedDocument object" requirement)."""

    raw: RawDocument
    ocr_metadata: OCRMetadata
    layout: DocumentLayout
    #: Whether this is a drawing or prose, and whether its text was scanned.
    #: Lives here rather than on `RawDocument` on purpose: `RawDocument` is
    #: the loaders' output contract, and a loader cannot answer this -- the
    #: question is only decidable once the file and the extracted text can be
    #: compared. Defaults to prose so every existing construction site,
    #: including the ones in tests, keeps its current behaviour.
    classification: ContentClassification = field(
        default_factory=lambda: ContentClassification(
            kind=ContentKind.PROSE, reason="not classified"
        )
    )

    @property
    def full_text(self) -> str:
        return self.raw.full_text

    @property
    def content_kind(self) -> ContentKind:
        """The document-level summary -- MIXED whenever its pages disagree.

        Routing for anything that belongs to a specific page should use
        `kind_for_page` instead: a five-page PDF holding a specification, a
        schedule, a plotted sheet and a scan has no single honest answer, and
        collapsing it to one is what this replaced.
        """
        return self.classification.kind

    def kind_for_page(self, page_number: int | None) -> ContentKind:
        return self.classification.kind_for_page(page_number)
