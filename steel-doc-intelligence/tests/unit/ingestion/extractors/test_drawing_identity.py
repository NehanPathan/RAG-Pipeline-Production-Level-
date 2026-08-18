"""Which drawing a document *is*, as opposed to which it mentions.

Getting this wrong files a document as a revision of a sheet it merely
cross-references -- superseding an unrelated drawing and demoting the real
current revision of it. So the rule is to answer only when the evidence is
unambiguous, and these tests are mostly about the cases where it must refuse.
"""

from __future__ import annotations

import pytest

from src.ingestion.extractors.drawing_identity import DrawingIdentity, extract_drawing_identity
from src.ingestion.extractors.models import (
    EntityExtractionResult,
    EntityOccurrence,
    EntitySource,
    SteelEntity,
    SteelEntityType,
)


def _entity(
    kind: SteelEntityType, canonical: str, count: int = 1, confidence: float = 0.85
) -> SteelEntity:
    return SteelEntity(
        type=kind,
        canonical=canonical,
        confidence=confidence,
        occurrences=tuple(
            EntityOccurrence(raw=canonical, source=EntitySource.REGEX) for _ in range(count)
        ),
    )


def _result(*entities: SteelEntity) -> EntityExtractionResult:
    return EntityExtractionResult(entities=list(entities))


class TestIdentification:
    def test_the_repeated_number_wins_over_cross_references(self):
        """A sheet's own number is in the title block and usually the border
        too; a cross-reference is mentioned once."""
        result = _result(
            _entity(SteelEntityType.DRAWING_NUMBER, "S-104", count=3),
            _entity(SteelEntityType.DRAWING_NUMBER, "S-101", count=1),
            _entity(SteelEntityType.DRAWING_NUMBER, "S-207", count=1),
            _entity(SteelEntityType.REVISION, "C"),
        )

        identity = extract_drawing_identity(result)

        assert identity is not None
        assert identity.drawing_number == "S-104"
        assert identity.revision_label == "C"

    def test_the_reason_names_the_evidence(self):
        """A register that silently picks between candidates cannot be audited."""
        result = _result(
            _entity(SteelEntityType.DRAWING_NUMBER, "S-104", count=3),
            _entity(SteelEntityType.REVISION, "C"),
        )

        identity = extract_drawing_identity(result)

        assert identity is not None
        assert "S-104" in identity.reason
        assert "3x" in identity.reason

    def test_a_single_drawing_number_needs_no_tie_break(self):
        result = _result(
            _entity(SteelEntityType.DRAWING_NUMBER, "S-104"),
            _entity(SteelEntityType.REVISION, "A"),
        )

        identity = extract_drawing_identity(result)

        assert identity is not None
        assert identity.drawing_number == "S-104"


class TestRefusesToGuess:
    def test_equally_frequent_candidates_yield_nothing(self):
        """Choosing either would be a coin flip on the document's identity,
        and a wrong choice corrupts another drawing's revision family."""
        result = _result(
            _entity(SteelEntityType.DRAWING_NUMBER, "S-104", count=2),
            _entity(SteelEntityType.DRAWING_NUMBER, "S-207", count=2),
            _entity(SteelEntityType.REVISION, "C"),
        )

        assert extract_drawing_identity(result) is None

    def test_no_drawing_number_yields_nothing(self):
        result = _result(_entity(SteelEntityType.REVISION, "C"))

        assert extract_drawing_identity(result) is None

    def test_several_revisions_yield_no_revision(self):
        """A revision table lists every past revision, so three labels on a
        sheet says nothing about which one it is."""
        result = _result(
            _entity(SteelEntityType.DRAWING_NUMBER, "S-104", count=3),
            _entity(SteelEntityType.REVISION, "A"),
            _entity(SteelEntityType.REVISION, "B"),
            _entity(SteelEntityType.REVISION, "C"),
        )

        identity = extract_drawing_identity(result)

        assert identity is not None
        assert identity.drawing_number == "S-104"
        assert identity.revision_label is None
        assert "no revision" in identity.reason

    def test_an_empty_result_yields_nothing(self):
        assert extract_drawing_identity(_result()) is None


class TestRoundTrip:
    """The identity is stored in `custom_metadata` and read back by the
    pipeline, so it has to survive a JSONB round-trip."""

    def test_a_full_identity_survives(self):
        original = DrawingIdentity("S-104", "C", "because")

        assert DrawingIdentity.from_dict(original.to_dict()) == original

    def test_a_missing_revision_survives(self):
        original = DrawingIdentity("S-104", None, "because")

        assert DrawingIdentity.from_dict(original.to_dict()) == original

    @pytest.mark.parametrize(
        "stored", [None, {}, {"drawing_number": ""}, {"revision_label": "C"}, "not a dict"]
    )
    def test_unusable_stored_data_yields_nothing(self, stored):
        assert DrawingIdentity.from_dict(stored) is None


class TestAgainstRealSheetText:
    """End to end from the extractor, which is where the identity actually
    comes from in production."""

    def test_a_title_block_with_cross_references(self):
        from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor

        sheet = (
            "DRAWING NO: S-104\n"
            "REV: C\n"
            "SCALE 1:100\n"
            "ROOF FRAMING PLAN\n"
            "SEE DWG S-101 FOR SECTION\n"
            "REFER TO DRAWING NO: S-207\n"
            "DRAWING NO: S-104\n"
            "JOB NO: 2024-0117\n"
        )

        identity = extract_drawing_identity(RegexSteelEntityExtractor().extract_sync(sheet))

        assert identity is not None
        assert identity.drawing_number == "S-104"
        assert identity.revision_label == "C"

    def test_a_drawing_number_is_not_glued_to_the_following_word(self):
        """Regression: the sheet-variant suffix accepted `[\\s\\-]`, and `\\s`
        matches a newline, so a title block reading

            DRAWING NO: S-104
            REV: C

        extracted `S-104-REV`. The same number followed by different words
        became several distinct entities, so a sheet's own number could never
        be identified by repetition -- and the `drawing_number` facet filled
        with values matching nothing.
        """
        from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor

        result = RegexSteelEntityExtractor().extract_sync(
            "DRAWING NO: S-104\nREV: C\nSEE DWG S-101 FOR SECTION\nJOB NO: 2024-0117"
        )

        numbers = {e.canonical for e in result.entities if e.type is SteelEntityType.DRAWING_NUMBER}
        assert numbers == {"S-104", "S-101"}

    def test_a_genuine_sheet_variant_suffix_is_kept(self):
        """`S-104-A` is a real sheet number; only whitespace joins were wrong."""
        from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor

        result = RegexSteelEntityExtractor().extract_sync("DRAWING NO: S-104-A\nREV: B")

        numbers = {e.canonical for e in result.entities if e.type is SteelEntityType.DRAWING_NUMBER}
        assert numbers == {"S-104-A"}
