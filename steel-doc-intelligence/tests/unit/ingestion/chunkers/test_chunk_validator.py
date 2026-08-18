import uuid

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.ingestion.chunkers.chunk_validator import ChunkValidator


def _chunk(content: str, position: int = 0) -> DocumentChunk:
    return DocumentChunk(document_id=uuid.uuid4(), content=content, position=position)


def test_valid_chunk_passes():
    validator = ChunkValidator()
    result = validator.validate([_chunk("This is a perfectly normal chunk of text about refunds.")])
    assert len(result.valid) == 1
    assert result.rejected == []


def test_too_small_chunk_rejected():
    validator = ChunkValidator(min_chars=20)
    result = validator.validate([_chunk("tiny")])
    assert result.valid == []
    assert result.rejected[0][1] == "too_small"


def test_whitespace_only_chunk_rejected():
    validator = ChunkValidator()
    result = validator.validate([_chunk("     \n\t  ")])
    assert result.rejected[0][1] == "whitespace_only"


def test_duplicate_content_rejected_after_first_occurrence():
    validator = ChunkValidator()
    chunks = [_chunk("The same exact sentence appears twice."), _chunk("The same exact sentence appears twice.")]
    result = validator.validate(chunks)
    assert len(result.valid) == 1
    assert result.rejected[0][1] == "duplicate"


def test_ocr_garbage_rejected_on_low_alnum_ratio():
    validator = ChunkValidator()
    result = validator.validate([_chunk("%%%$$$ ##@@ &&**!! ^^~~ ][;;")])
    assert result.rejected[0][1] == "ocr_garbage"


def test_low_ocr_confidence_rejects_otherwise_valid_chunk():
    validator = ChunkValidator(min_ocr_confidence=0.5)
    result = validator.validate(
        [_chunk("This text looks totally fine on its own.")], ocr_confidence=0.2
    )
    assert result.rejected[0][1] == "low_ocr_confidence"


def test_high_ocr_confidence_does_not_reject():
    validator = ChunkValidator(min_ocr_confidence=0.5)
    result = validator.validate(
        [_chunk("This text looks totally fine on its own.")], ocr_confidence=0.9
    )
    assert len(result.valid) == 1


class TestPerChunkOCRConfidence:
    """Regression: the document-average OCR confidence was applied to every
    chunk, so one bad average discarded the whole document -- while the
    document still reported status=indexed with zero chunks."""

    @staticmethod
    def _chunk(content: str, confidence: float | None) -> DocumentChunk:
        return DocumentChunk(
            document_id=uuid.uuid4(),
            content=content,
            position=0,
            chunk_metadata=ChunkMetadata(ocr_confidence=confidence),
        )

    def test_legible_chunk_survives_a_bad_document_average(self):
        legible = self._chunk("Beam schedule: ISMB 300 at grid line 4, span 6000 mm.", 0.92)

        result = ChunkValidator(min_ocr_confidence=0.35).validate(
            [legible], ocr_confidence=0.28
        )

        assert result.valid == [legible], (
            "a clearly-read chunk must not be discarded because other pages scanned badly"
        )

    def test_illegible_chunk_is_still_rejected_on_its_own_confidence(self):
        smudged = self._chunk("Beam schedule: ISMB 300 at grid line 4, span 6000 mm.", 0.10)

        result = ChunkValidator(min_ocr_confidence=0.35).validate(
            [smudged], ocr_confidence=0.95
        )

        assert result.rejected[0][1] == "low_ocr_confidence"

    def test_document_average_is_the_fallback_when_a_chunk_has_none(self):
        unmeasured = self._chunk("Text with no per-chunk confidence recorded.", None)

        result = ChunkValidator(min_ocr_confidence=0.35).validate(
            [unmeasured], ocr_confidence=0.20
        )

        assert result.rejected[0][1] == "low_ocr_confidence"

    def test_drawings_use_a_lower_floor(self):
        """Engineering drawings scan at 0.30-0.50 because of rotated dimension
        text, hatching and leader lines. The standard floor rejects all of them."""
        drawing_chunk = self._chunk("DRG No. S-101  REV C  GRID 4  ISMB 300", 0.22)

        rejected = ChunkValidator(min_ocr_confidence=0.35).validate([drawing_chunk])
        kept = ChunkValidator(
            min_ocr_confidence=0.35, min_ocr_confidence_drawing=0.15
        ).validate([drawing_chunk], is_drawing=True)

        assert rejected.valid == []
        assert kept.valid == [drawing_chunk]


class TestSparseTableChunks:
    """Regression: a schedule or BOM row window is mostly pipes and empty
    cells, which scores below the alnum ratio and was dropped as OCR garbage."""

    def test_sparse_schedule_row_window_is_kept(self):
        sparse = DocumentChunk(
            document_id=uuid.uuid4(),
            content="| M1 |  |  | 2 |\n| M2 |  |  | 4 |",
            position=0,
            chunk_type=ChunkType.TABLE,
        )

        result = ChunkValidator().validate([sparse])

        assert result.valid == [sparse]

    def test_prose_is_still_held_to_the_alnum_ratio(self):
        garbage = DocumentChunk(
            document_id=uuid.uuid4(),
            content="%%%$$$ ##@@ &&**!! ^^~~ ][;;",
            position=0,
            chunk_type=ChunkType.CHILD,
        )

        result = ChunkValidator().validate([garbage])

        assert result.rejected[0][1] == "ocr_garbage"


class TestParentChildDeduplication:
    """Regression: a short section produces a child whose text is identical to
    its parent, and rejecting it as a duplicate left the document with no
    embeddable chunks at all."""

    @staticmethod
    def _chunk(content: str, chunk_type: ChunkType) -> DocumentChunk:
        return DocumentChunk(
            document_id=uuid.uuid4(), content=content, position=0, chunk_type=chunk_type
        )

    def test_child_identical_to_parent_is_kept(self):
        text = "Enterprise customers in the EU have a 60 day refund window."
        chunks = [
            self._chunk(text, ChunkType.PARENT),
            self._chunk(text, ChunkType.CHILD),
        ]

        result = ChunkValidator().validate(chunks)

        assert len(result.valid) == 2, "the child must survive; only children get embedded"
        assert {c.chunk_type for c in result.valid} == {ChunkType.PARENT, ChunkType.CHILD}

    def test_short_document_yields_an_embeddable_chunk(self):
        """The property that actually matters: something embeddable survives."""
        text = "A short policy note that fits inside a single child window."
        result = ChunkValidator().validate(
            [self._chunk(text, ChunkType.PARENT), self._chunk(text, ChunkType.CHILD)]
        )
        embeddable = [
            c for c in result.valid
            if c.chunk_type in (ChunkType.CHILD, ChunkType.TABLE, ChunkType.STANDALONE)
        ]
        assert embeddable, "no embeddable chunk means the document is invisible to vector search"

    def test_duplicate_children_are_still_rejected(self):
        text = "Repeated boilerplate paragraph appearing twice in one document."
        result = ChunkValidator().validate(
            [self._chunk(text, ChunkType.CHILD), self._chunk(text, ChunkType.CHILD)]
        )
        assert len(result.valid) == 1
        assert result.rejected[0][1] == "duplicate"

    def test_duplicate_parents_are_still_rejected(self):
        text = "Repeated boilerplate paragraph appearing twice in one document."
        result = ChunkValidator().validate(
            [self._chunk(text, ChunkType.PARENT), self._chunk(text, ChunkType.PARENT)]
        )
        assert len(result.valid) == 1
        assert result.rejected[0][1] == "duplicate"
