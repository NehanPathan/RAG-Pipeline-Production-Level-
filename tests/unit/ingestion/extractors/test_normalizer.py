import pytest

from src.ingestion.extractors.normalizer import (
    canonical_bolt,
    canonical_grade,
    canonical_identifier,
    canonical_section,
    canonical_unit,
    to_si,
)


class TestCanonicalSection:
    """The property that matters: every way a designation is written on a
    real drawing set must reduce to one key, or the specification and the
    schedule stop finding each other."""

    @pytest.mark.parametrize(
        "prefix,size",
        [
            ("ISMB", "300"),
            ("ISMB", " 300"),
            ("I.S.M.B.", "300"),
            ("ismb", "300"),
            ("ISMB", "-300"),
        ],
    )
    def test_spellings_of_ismb_300_agree(self, prefix, size):
        assert canonical_section(prefix, size) == "ISMB 300"

    @pytest.mark.parametrize(
        "size,expected",
        [
            ("300x140", "ISMB 300X140"),
            ("300 x 140", "ISMB 300X140"),
            ("300×140", "ISMB 300X140"),  # noqa: RUF001 - U+00D7 is written on real drawings; matching it is the point
            ("75x75x6", "ISMB 75X75X6"),
        ],
    )
    def test_multiplication_signs_are_normalised(self, size, expected):
        assert canonical_section("ISMB", size) == expected

    def test_a_decimal_in_the_size_survives(self):
        """AISC weights are decimal. `C10x15.3` collapsed to `C 10X153` would
        be a different section -- the dot-stripping that handles `I.S.M.B.`
        must not reach the size."""
        assert canonical_section("C", "10x15.3") == "C 10X15.3"


class TestCanonicalGrade:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Fe 415", "Fe 415"),
            ("fe-415", "Fe 415"),
            ("FE415", "Fe 415"),
            ("S355", "S355"),
            ("s355 j2", "S355 J2"),
            ("S 355 JR", "S355 JR"),
            ("IS 2062 E250", "E 250"),
            ("E350", "E 350"),
            ("E 350 B", "E 350 B"),
            ("A36", "A36"),
            ("a 572", "A572"),
        ],
    )
    def test_grades_canonicalise(self, raw, expected):
        assert canonical_grade(raw) == expected


class TestCanonicalBolt:
    def test_diameter_only(self):
        assert canonical_bolt("20") == "M20"

    def test_diameter_and_class(self):
        assert canonical_bolt("20", "8.8") == "M20 8.8"

    def test_length_is_kept(self):
        """A bolt schedule distinguishes M20x60 from M20x80; dropping the
        length would merge two different line items."""
        assert canonical_bolt("20", "8.8", "60") == "M20X60 8.8"


class TestUnits:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("mm", "mm"),
            ("MPa", "MPa"),
            ("N/mm2", "MPa"),
            ("N/mm²", "MPa"),
            ("n / mm2", "MPa"),
            ("kN", "kN"),
            ("kNm", "kNm"),
        ],
    )
    def test_unit_spellings_agree(self, raw, expected):
        assert canonical_unit(raw) == expected

    def test_unknown_unit_passes_through(self):
        assert canonical_unit("furlongs") == "furlongs"

    @pytest.mark.parametrize(
        "value,unit,expected",
        [
            (1.0, "m", (1000.0, "mm")),
            (2.5, "cm", (25.0, "mm")),
            (1.0, "in", (25.4, "mm")),
            (250.0, "MPa", (250.0, "MPa")),
            (1000.0, "N", (1.0, "kN")),
        ],
    )
    def test_conversion_to_si(self, value, unit, expected):
        assert to_si(value, unit) == expected

    def test_unconvertible_unit_returns_none(self):
        """Better to record the value as written than invent an equivalence."""
        assert to_si(1.0, "furlongs") is None


class TestCanonicalIdentifier:
    @pytest.mark.parametrize(
        "raw,expected",
        [("s-101", "S-101"), ("S 101", "S-101"), ("S  101", "S-101"), ("s_101", "S-101")],
    )
    def test_separators_normalise_to_hyphen(self, raw, expected):
        assert canonical_identifier(raw) == expected

    def test_a_number_with_no_separator_keeps_none(self):
        """Inserting a separator that was never there would invent an
        identifier that does not exist in the client's drawing register."""
        assert canonical_identifier("S101") == "S101"
