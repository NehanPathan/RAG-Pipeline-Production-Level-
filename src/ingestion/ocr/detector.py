from __future__ import annotations

from dataclasses import dataclass, field

from src.ingestion.loaders.base import RawDocument

# Always text-native -- these formats are never scanned images, so OCR would
# only waste time (and, for an LLM enrichment step downstream, risk garbling
# text that was already extracted correctly).
_NEVER_OCR_EXTENSIONS = {"docx", "doc", "md", "html", "htm", "txt"}

# Images have no native text layer at all -- there is nothing for a
# DocumentLoader to extract, so OCR is the only source of text.
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}


@dataclass
class OCRDecision:
    required: bool
    reason: str
    # Specific 1-indexed page numbers that need OCR. Empty when `required`
    # is False, or when OCR applies to the whole "document" rather than
    # individual pages (e.g. a standalone image file).
    pages_required: list[int] = field(default_factory=list)


class OCRDetector:
    """Decides whether OCR must run for a given loaded document, instead of
    forcing OCR on every document (Part 1's explicit requirement).

    Checks text density **per page**, not as a single document-wide average
    -- a mixed document (some pages richly native-text, one page a scanned
    image) would otherwise dilute the average enough to skip OCR entirely
    for the one page that actually needs it. Found via a real user upload
    during Phase 4A verification: a 2-page PDF where Docling's own internal
    OCR happened to already cover the sparse page, masking the gap -- see
    docs/architecture/12_phase4a_design_review.md.
    """

    def __init__(
        self, min_words_per_page: float = 10.0, ocr_pdfs_without_text_layer: bool = True
    ) -> None:
        self._min_words_per_page = min_words_per_page
        self._ocr_pdfs_without_text_layer = ocr_pdfs_without_text_layer

    def detect(
        self,
        raw_document: RawDocument,
        file_type: str,
        has_native_text_layer: bool | None = None,
    ) -> OCRDecision:
        """`has_native_text_layer` is the caller's answer to "did this text
        come from the file, or from an OCR engine?" -- see
        `DrawingContentDetector`. `None` means the caller has no opinion and
        only the word-density rules below apply.
        """
        ext = file_type.lower().lstrip(".")

        if ext in _NEVER_OCR_EXTENSIONS:
            return OCRDecision(required=False, reason=f"'{ext}' is always text-native")

        if ext in _IMAGE_EXTENSIONS:
            return OCRDecision(
                required=True, reason="image file has no extractable text", pages_required=[1]
            )

        if ext == "pdf":
            # Word density cannot see this case. Docling runs its own OCR on
            # a scanned page and returns the result as ordinary text, so a
            # scan and a plotted sheet both arrive looking text-rich and both
            # get skipped here. The difference is that the scan's text has no
            # confidence score and no word coordinates -- the two things the
            # chunk validator and region highlighting are built on. Running
            # our own OCR is what recovers them.
            if has_native_text_layer is False and self._ocr_pdfs_without_text_layer:
                pages = list(range(1, (raw_document.page_count or 1) + 1))
                return OCRDecision(
                    required=True,
                    reason=(
                        "PDF has no native text layer: any text present is OCR-derived "
                        "and carries no confidence or word geometry"
                    ),
                    pages_required=pages,
                )

            sparse_pages = self._find_sparse_pages(raw_document)
            if sparse_pages:
                total_pages = raw_document.page_count or len(sparse_pages)
                return OCRDecision(
                    required=True,
                    reason=(
                        f"{len(sparse_pages)} of {total_pages} page(s) have low text "
                        f"density (< {self._min_words_per_page:.0f} words/page)"
                    ),
                    pages_required=sparse_pages,
                )
            return OCRDecision(
                required=False, reason="searchable PDF (sufficient extracted text on every page)"
            )

        # Unrecognized extension: skip rather than force OCR on something a
        # future loader might already handle natively.
        return OCRDecision(
            required=False, reason=f"unrecognized extension '{ext}', defaulting to skip"
        )

    def _find_sparse_pages(self, raw_document: RawDocument) -> list[int]:
        page_count = raw_document.page_count or 0
        if page_count <= 0:
            # Can't attribute words to individual pages at all -- fall back
            # to the whole document as a single unit (matches pre-per-page
            # behavior for degenerate/unknown-page-count inputs).
            word_count = raw_document.word_count or 0
            return [1] if word_count < self._min_words_per_page else []

        words_by_page: dict[int, int] = dict.fromkeys(range(1, page_count + 1), 0)
        for block in raw_document.text_blocks:
            if block.page_number is None or block.page_number not in words_by_page:
                continue
            words_by_page[block.page_number] += len(block.text.split())

        return [
            page
            for page, words in sorted(words_by_page.items())
            if words < self._min_words_per_page
        ]
