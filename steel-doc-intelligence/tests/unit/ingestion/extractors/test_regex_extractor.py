import pytest

from src.ingestion.extractors.models import SteelEntityType
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor


@pytest.fixture(scope="module")
def extractor() -> RegexSteelEntityExtractor:
    return RegexSteelEntityExtractor()


def _canonicals(extractor, text, entity_type=None):
    result = extractor.extract_sync(text)
    return {
        e.canonical for e in result.entities if entity_type is None or e.type is entity_type
    }


class TestSectionDesignations:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Provide ISMB 300 at grid 4.", "ISMB 300"),
            ("Beam is ISMB300 throughout.", "ISMB 300"),
            ("Use I.S.M.B. 300 here.", "ISMB 300"),
            ("Channel ISMC 200 both sides.", "ISMC 200"),
            ("European section IPE 400.", "IPE 400"),
            ("Column HEB 300 typical.", "HEB 300"),
            ("Angle ISA 75x75x6 cleat.", "ISA 75X75X6"),
            ("Hollow section SHS 100x100x5.", "SHS 100X100X5"),
        ],
    )
    def test_designations_are_extracted_and_canonicalised(self, extractor, text, expected):
        assert expected in _canonicals(extractor, text, SteelEntityType.SECTION_DESIGNATION)

    def test_every_spelling_reduces_to_one_entity(self, extractor):
        """The whole point of canonicalisation: a document that writes the
        same section three ways yields one searchable fact, seen three times."""
        text = "ISMB 300 at grid 3, ISMB300 at grid 4, and I.S.M.B.-300 at grid 5."

        sections = [
            e
            for e in extractor.extract_sync(text).entities
            if e.type is SteelEntityType.SECTION_DESIGNATION
        ]

        assert len(sections) == 1
        assert sections[0].canonical == "ISMB 300"
        assert sections[0].count == 3, "all three occurrences must be kept as provenance"

    def test_a_published_size_is_more_confident_than_an_unusual_one(self, extractor):
        known = extractor.extract_sync("ISMB 300").entities[0]
        unusual = extractor.extract_sync("ISMB 317").entities[0]

        assert known.confidence > unusual.confidence

    def test_the_standard_is_carried_with_the_designation(self, extractor):
        entity = extractor.extract_sync("ISMB 300").entities[0]

        assert entity.attributes["standard"] == "IS 808"
        assert entity.attributes["kind"] == "i_beam"

    def test_an_unknown_prefix_is_not_a_section(self, extractor):
        assert _canonicals(extractor, "ZZZZ 300 somewhere", SteelEntityType.SECTION_DESIGNATION) == set()


class TestFalsePositives:
    """The permissive-pattern failure mode. A section regex loose enough to
    read `W14x90` will read ordinary prose as sections unless gated."""

    def test_prose_containing_a_bare_letter_and_number_is_not_a_section(self, extractor):
        found = _canonicals(
            extractor, "The span is 12 metres between grids.", SteelEntityType.SECTION_DESIGNATION
        )
        assert found == set()

    def test_grid_references_are_not_part_marks(self, extractor):
        """Part marks are gated on a preceding key word for exactly this."""
        found = _canonicals(extractor, "Between grid A1 and grid B2.", SteelEntityType.PART_MARK)
        assert found == set()

    def test_a_scale_ratio_is_not_a_drawing_number(self, extractor):
        found = _canonicals(extractor, "Scale 1:100 at A1.", SteelEntityType.DRAWING_NUMBER)
        assert found == set()

    def test_a_drawing_number_is_not_an_aisc_section(self, extractor):
        """`S-104` appears on every second steel drawing, and a naive pattern
        reads it as an American Standard beam. Single-letter AISC families
        always carry a multiplication sign, so requiring one separates the
        real designations from the identifiers."""
        found = _canonicals(
            extractor, "DRAWING NO: S-104 REV B", SteelEntityType.SECTION_DESIGNATION
        )
        assert found == set()

    @pytest.mark.parametrize("text,expected", [("W14x90", "W 14X90"), ("C10x15.3", "C 10X15.3")])
    def test_genuine_aisc_designations_still_match(self, extractor, text, expected):
        assert expected in _canonicals(extractor, text, SteelEntityType.SECTION_DESIGNATION)


class TestGrades:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Steel grade Fe 415 throughout.", "Fe 415"),
            ("Material to IS 2062 E250.", "E 250"),
            ("Plate in S355 J2.", "S355 J2"),
            ("Sections in A992.", "A992"),
        ],
    )
    def test_grades_are_extracted(self, extractor, text, expected):
        assert expected in _canonicals(extractor, text, SteelEntityType.STEEL_GRADE)

    def test_yield_strength_is_recorded_as_a_grade_claim(self, extractor):
        entities = [
            e
            for e in extractor.extract_sync("Fy = 250 MPa minimum.").entities
            if e.type is SteelEntityType.STEEL_GRADE
        ]

        assert entities and entities[0].attributes["yield_mpa"] == "250"

    def test_yield_in_alternative_units_normalises(self, extractor):
        """`N/mm2` and `MPa` are the same claim and must agree."""
        a = extractor.extract_sync("Fy = 250 N/mm2").entities
        b = extractor.extract_sync("Fy = 250 MPa").entities

        assert [e.canonical for e in a if e.type is SteelEntityType.STEEL_GRADE] == [
            e.canonical for e in b if e.type is SteelEntityType.STEEL_GRADE
        ]

    def test_a_known_grade_carries_its_standard(self, extractor):
        entity = next(
            e
            for e in extractor.extract_sync("Grade Fe 415").entities
            if e.type is SteelEntityType.STEEL_GRADE
        )
        assert entity.attributes["standard"] == "IS 1786"


class TestBoltsAndWelds:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Use M20 8.8 bolts.", "M20 8.8"),
            ("Bolts M20x60 grade 8.8.", "M20X60 8.8"),
            ("Connection with M24 class 10.9.", "M24 10.9"),
        ],
    )
    def test_bolt_specs(self, extractor, text, expected):
        assert expected in _canonicals(extractor, text, SteelEntityType.BOLT_SPEC)

    def test_bolt_class_brings_its_strength(self, extractor):
        entity = next(
            e
            for e in extractor.extract_sync("M20 8.8 bolts").entities
            if e.type is SteelEntityType.BOLT_SPEC
        )
        assert entity.attributes["tensile_mpa"] == "800"

    def test_hsfg_is_recognised(self, extractor):
        assert "HSFG" in _canonicals(extractor, "HSFG bolts throughout.", SteelEntityType.BOLT_SPEC)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Provide 6mm fillet weld all round.", "6mm FILLET weld"),
            ("8 mm CJP weld at splice.", "8mm CJP weld"),
            ("FW6 typical.", "6mm FILLET weld"),
        ],
    )
    def test_weld_specs(self, extractor, text, expected):
        assert expected in _canonicals(extractor, text, SteelEntityType.WELD_SPEC)


class TestDimensionsAndLoads:
    def test_a_dimension_is_extracted_with_si_normalisation(self, extractor):
        entity = next(
            e
            for e in extractor.extract_sync("Clear span 6.5 m between grids.").entities
            if e.type is SteelEntityType.DIMENSION
        )
        assert entity.attributes["value_si"] == "6500.0"
        assert entity.attributes["unit_si"] == "mm"

    def test_a_load_needs_context_to_be_a_load(self, extractor):
        """Without the context gate every number on a drawing becomes a load."""
        with_context = _canonicals(
            extractor, "Design load capacity 250 kN.", SteelEntityType.LOAD_CAPACITY
        )
        without_context = _canonicals(
            extractor, "Move the crane 250 kN to the left.", SteelEntityType.LOAD_CAPACITY
        )

        assert "250 kN" in with_context
        assert without_context == set()

    def test_a_load_is_less_confident_than_a_plain_dimension(self, extractor):
        """It is only a load because of a nearby word, which is weaker
        evidence than notation, and the confidence should say so."""
        load = next(
            e
            for e in extractor.extract_sync("Shear capacity 250 kN").entities
            if e.type is SteelEntityType.LOAD_CAPACITY
        )
        dimension = next(
            e
            for e in extractor.extract_sync("Depth 300 mm").entities
            if e.type is SteelEntityType.DIMENSION
        )

        assert load.confidence < dimension.confidence


class TestIdentifiers:
    @pytest.mark.parametrize(
        "text,entity_type,expected",
        [
            ("DWG NO: S-101", SteelEntityType.DRAWING_NUMBER, "S-101"),
            ("Drawing No. S 101", SteelEntityType.DRAWING_NUMBER, "S-101"),
            ("JOB NO 2024-0117", SteelEntityType.PROJECT_NUMBER, "2024-0117"),
            ("MARK B-14", SteelEntityType.PART_MARK, "B-14"),
            ("REV C", SteelEntityType.REVISION, "C"),
        ],
    )
    def test_gated_identifiers(self, extractor, text, entity_type, expected):
        assert expected in _canonicals(extractor, text, entity_type)


class TestProvenance:
    def test_occurrences_record_where_each_match_was_found(self, extractor):
        text = "Beam ISMB 300 spans 6 m."

        entity = next(
            e
            for e in extractor.extract_sync(text, page_number=7).entities
            if e.type is SteelEntityType.SECTION_DESIGNATION
        )
        occurrence = entity.occurrences[0]

        assert occurrence.page_number == 7
        assert text[occurrence.char_start : occurrence.char_end] == "ISMB 300"
        assert occurrence.raw == "ISMB 300"

    def test_result_exposes_canonicals_for_indexing(self, extractor):
        """These are appended to document tags and chunk entity_canonicals,
        both already indexed as keyword fields -- a filterable steel index
        for no new index schema."""
        result = extractor.extract_sync("ISMB 300 in Fe 415 with M20 8.8 bolts.")

        canonicals = result.canonicals(SteelEntityType.SECTION_DESIGNATION)

        assert canonicals == ["ISMB 300"]

    def test_a_realistic_title_block_yields_the_expected_facts(self, extractor):
        text = (
            "DRAWING NO: S-104   REV B   JOB NO 2024-0117\n"
            "BEAM SCHEDULE\n"
            "B-14  ISMB 300  Fe 415  span 6000 mm  shear capacity 180 kN\n"
            "Connection: M20x60 grade 8.8, 6mm fillet weld. Steel to IS 2062."
        )

        result = extractor.extract_sync(text)
        by_type = result.by_type

        assert by_type.get("drawing_number") == 1
        assert by_type.get("section_designation") == 1
        assert by_type.get("steel_grade", 0) >= 1
        assert by_type.get("bolt_spec") == 1
        assert by_type.get("weld_spec") == 1
        assert by_type.get("load_capacity") == 1
