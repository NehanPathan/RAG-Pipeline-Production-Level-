import uuid

from src.domain.entities.document import DocumentChunk
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
