"""Deterministic steel-entity extraction.

Regex plus a gazetteer, in preference to a statistical NER model, for three
reasons that matter in this domain:

* **Auditability.** A structural engineer can be shown exactly why `ISMB 300`
  was extracted and exactly what would have to change for it not to be. A
  fine-tuned model cannot offer that, and this is a domain where a wrong
  section designation is a safety issue rather than a search-quality one.
* **Precision on the things that matter.** Designations, grades, bolt specs
  and dimensions are *notation*, not natural language. Notation is what
  regex is for.
* **Cost.** Forty compiled patterns and a YAML file do this work with no
  model download, no inference and no per-document API call.

What regex is bad at -- "the beam supporting grid line 3", connection
descriptions, design assumptions -- is left to the LLM pass, which is
clamped to a lower confidence so a deterministic match always wins a tie.

Confidence levels, applied consistently:
  0.95  gazetteer-validated (the family or grade is a real one)
  0.85  pattern-only (unambiguous notation, unknown family)
  0.60  context-gated (a number that is only a load because of a nearby word)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from src.ingestion.extractors.gazetteer import Gazetteer, get_gazetteer
from src.ingestion.extractors.models import (
    EntityExtractionResult,
    EntityOccurrence,
    EntitySource,
    SteelEntity,
    SteelEntityType,
)
from src.ingestion.extractors.normalizer import (
    canonical_bolt,
    canonical_grade,
    canonical_identifier,
    canonical_section,
    canonical_unit,
    to_si,
)

# What each extraction pass yields, before merging: the entity's type and
# canonical form, how confident the pass is, its attributes, and the one
# place it was seen.
Found = tuple[SteelEntityType, str, float, dict[str, str], EntityOccurrence]

CONFIDENCE_GAZETTEER = 0.95
CONFIDENCE_PATTERN = 0.85
CONFIDENCE_CONTEXT = 0.60

# A size is one or more numbers joined by a multiplication sign: `300`,
# `300x140`, `75x75x6`, `14x90`. Decimals appear in imperial designations.
_SIZE = r"\d{1,4}(?:\.\d+)?(?:\s*[xX×]\s*\d{1,4}(?:\.\d+)?){0,3}"  # noqa: RUF001 - U+00D7 is written on real drawings; matching it is the point

# The compound form only: at least one multiplication sign. Required for
# single-letter families (see below), where a bare number is far more likely
# to be a drawing number or a grid reference than a section.
_COMPOUND_SIZE = r"\d{1,4}(?:\.\d+)?(?:\s*[xX×]\s*\d{1,4}(?:\.\d+)?){1,3}"  # noqa: RUF001 - U+00D7 is written on real drawings; matching it is the point


def _dotted(prefix: str) -> str:
    """`ISMB` -> `I\\.?S\\.?M\\.?B\\.?`.

    OCR of a title block routinely returns `I.S.M.B.-300`, because that is
    how the abbreviation is punctuated on older drawings. Accepting the dots
    in the pattern is what lets the normalizer collapse it onto the same
    canonical key as `ISMB 300`.
    """
    return r"\.?".join(re.escape(ch) for ch in prefix) + r"\.?"

_GRADE_PATTERNS = (
    re.compile(r"\bFe[\s\-]?(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bIS[\s\-]?2062[\s\-]*(E[\s\-]?\d{3}[\s\-]?[ABC]?)\b", re.IGNORECASE),
    re.compile(r"\bS[\s\-]?(235|275|355|420|460)[\s\-]?(J[RO0-2]|K2|NL|ML|N|M)?\b"),
    re.compile(r"\bA[\s\-]?(36|500|572|992)\b"),
)

# Fy = 250 MPa. Captured as a grade rather than a dimension because that is
# what it designates -- and it lets "Fy = 415" and "Fe 415" agree.
_YIELD = re.compile(
    r"\bF\s*[yu]\s*[=:]?\s*(\d{2,4})\s*(MPa|N\s*/\s*mm2|N\s*/\s*mm²|ksi)\b", re.IGNORECASE
)

_BOLT = re.compile(
    r"\bM\s?(\d{1,2})(?:\s*[x×]\s*(\d{2,3}))?"  # noqa: RUF001 - U+00D7 is written on real drawings; matching it is the point
    r"(?:\s*(?:grade|gr\.?|class)?\s*(4\.6|4\.8|5\.6|5\.8|8\.8|10\.9|12\.9))?\b",
    re.IGNORECASE,
)
_HSFG = re.compile(r"\bHSFG\b|\bTC[\s\-]?bolts?\b", re.IGNORECASE)

_WELD_SIZE = re.compile(
    r"\b(\d{1,2})\s*mm\s*(fillet|butt|groove|CJP|PJP)\s*weld\b", re.IGNORECASE
)
_WELD_SHORT = re.compile(r"\bFW\s?(\d{1,2})\b")
_ELECTRODE = re.compile(r"\bE\s?(60|70|80|90|100)1[0-9]\b")

_DIMENSION = re.compile(
    r"(?<![\w.])(\d{1,6}(?:\.\d+)?)\s*"
    r"(mm|cm|m|kg/m|kg|kN/m|kNm|kN|MPa|N/mm2|N/mm²|ksi|t)(?![\w/])",
    re.IGNORECASE,
)

# A dimension is a load only when something nearby says so. Without this gate
# every number on a drawing becomes a load capacity.
_LOAD_CONTEXT = re.compile(
    r"(capacit|load|udl|reaction|shear|moment|axial|bearing|tension|compression)",
    re.IGNORECASE,
)
_LOAD_UNITS = {"kN", "kN/m", "kNm", "t"}
_LOAD_WINDOW = 45

# Identifiers are gated by a preceding key. Without the gate this pattern
# consumes every alphanumeric token on a drawing -- grid references, sheet
# sizes, scale ratios and all.
_IDENTIFIER_PATTERNS = {
    # The optional trailing group is a *sheet variant* -- `S-104-A`, `S-104-01`
    # -- and is joined by a hyphen, never by whitespace. It used to accept
    # `[\s\-]`, and `\s` matches a newline, so a title block reading
    #     DRAWING NO: S-104
    #     REV: C
    # extracted `S-104-REV`, and `SEE DWG S-101 FOR SECTION` extracted
    # `S-101-FOR`. Worse than cosmetic: the same sheet number followed by
    # different words became several distinct entities, so the drawing
    # number could never be identified by repetition, and the
    # `drawing_number` facet filled with values matching nothing.
    SteelEntityType.DRAWING_NUMBER: re.compile(
        r"\b(?:DWG|DRG|DRAWING|SHEET)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*"
        r"([A-Z]{0,4}[\s\-]?\d{1,5}(?:-[A-Z0-9]{1,4})?)\b",
        re.IGNORECASE,
    ),
    SteelEntityType.PROJECT_NUMBER: re.compile(
        r"\b(?:PROJECT|JOB|CONTRACT)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*"
        r"([A-Z]{0,4}[\s\-]?\d{2,6}(?:-[A-Z0-9]{1,4})?)\b",
        re.IGNORECASE,
    ),
    SteelEntityType.PART_MARK: re.compile(
        r"\b(?:MARK|PIECE|PART|MEMBER)\s*(?:NO\.?|#)?\s*[:\-]?\s*"
        r"([A-Z]{1,3}[\s\-]?\d{1,4})\b",
        re.IGNORECASE,
    ),
    SteelEntityType.REVISION: re.compile(
        r"\b(?:REV|REVISION)\s*(?:NO\.?|#)?\s*[:\-]?\s*([A-Z]|\d{1,2})\b",
        re.IGNORECASE,
    ),
}

_STANDARD_REF = re.compile(
    r"\b(IS|EN|BS|ASTM|AISC|AWS|ISO|DIN)\s*[:\-]?\s*(\d{2,5}(?:[\-\s]\d{1,4})?)\b"
)


class RegexSteelEntityExtractor:
    """The deterministic pass. Satisfies `EntityExtractor`."""

    def __init__(self, gazetteer: Gazetteer | None = None) -> None:
        self._gazetteer = gazetteer or get_gazetteer()
        prefixes = self._gazetteer.family_prefixes
        multi = [p for p in prefixes if len(p) > 1]
        single = [p for p in prefixes if len(p) == 1]

        # Multi-letter families are distinctive enough to accept a bare size
        # and a hyphen separator: nothing else in a structural document reads
        # like "ISMC-200".
        self._section_multi = re.compile(
            r"\b(" + "|".join(_dotted(p) for p in multi) + r")"
            r"[\s\-.]{0,2}(" + _SIZE + r")\b"
        )

        # Single-letter families (AISC W, S, C, L, and the like) are the
        # false-positive engine. `S-104` is a drawing number on every second
        # steel drawing, and a bare pattern reads it as an American Standard
        # beam. AISC designations always carry a multiplication sign
        # (`W14x90`, `L4x4x1/4`), so requiring one -- and refusing a hyphen
        # separator -- keeps the real ones and rejects the identifiers.
        self._section_single = re.compile(
            r"\b(" + "|".join(re.escape(p) for p in single) + r")"
            r"\s?(" + _COMPOUND_SIZE + r")\b"
        )

    async def extract(self, text: str, page_number: int | None = None) -> EntityExtractionResult:
        return self.extract_sync(text, page_number)

    def extract_sync(self, text: str, page_number: int | None = None) -> EntityExtractionResult:
        """Synchronous entry point.

        Chunk-level extraction runs this over every chunk during ingestion,
        where an await per chunk would buy nothing -- there is no I/O here.
        """
        found: list[Found] = []

        found.extend(self._sections(text, page_number))
        found.extend(self._grades(text, page_number))
        found.extend(self._bolts(text, page_number))
        found.extend(self._welds(text, page_number))
        found.extend(self._dimensions(text, page_number))
        found.extend(self._identifiers(text, page_number))
        found.extend(self._standards(text, page_number))

        return _merge(found)

    # -- individual passes -------------------------------------------------

    def _sections(self, text: str, page: int | None) -> Iterator[Found]:
        for pattern in (self._section_multi, self._section_single):
            yield from self._sections_for(pattern, text, page)

    def _sections_for(
        self, pattern: re.Pattern[str], text: str, page: int | None
    ) -> Iterator[Found]:
        for match in pattern.finditer(text):
            prefix, size = match.group(1), match.group(2)
            # The dotted spelling is matched as written; the gazetteer is
            # keyed on the undotted form.
            family = self._gazetteer.family(prefix.replace(".", ""))
            if family is None:
                continue

            canonical = canonical_section(prefix, size)
            attributes = {"standard": family.standard, "kind": family.kind}

            # A single-number size can be checked against the family's
            # published range. A match inside it is as certain as this gets;
            # one outside is still probably a section (fabricators use
            # non-standard sizes) but is not asserted with the same weight.
            leading = re.match(r"\d{1,4}", size.replace(" ", ""))
            confidence = CONFIDENCE_PATTERN
            if leading and family.sizes:
                depth = int(leading.group())
                if family.knows_size(depth):
                    confidence = CONFIDENCE_GAZETTEER
                    attributes["depth_mm"] = str(depth)
            elif not family.sizes:
                # Families whose designations are not in millimetres cannot be
                # range-checked; the family itself being real is the validation.
                confidence = CONFIDENCE_GAZETTEER

            yield (
                SteelEntityType.SECTION_DESIGNATION,
                canonical,
                confidence,
                attributes,
                _occurrence(match, page, EntitySource.GAZETTEER),
            )

    def _grades(self, text: str, page: int | None) -> Iterator[Found]:
        for pattern in _GRADE_PATTERNS:
            for match in pattern.finditer(text):
                canonical = canonical_grade(match.group(0))
                spec = self._gazetteer.grade(canonical)
                attributes = {}
                if spec:
                    attributes = {k: str(v) for k, v in spec.items()}
                yield (
                    SteelEntityType.STEEL_GRADE,
                    canonical,
                    CONFIDENCE_GAZETTEER if spec else CONFIDENCE_PATTERN,
                    attributes,
                    _occurrence(match, page, EntitySource.GAZETTEER if spec else EntitySource.REGEX),
                )

        for match in _YIELD.finditer(text):
            value, unit = float(match.group(1)), canonical_unit(match.group(2))
            si = to_si(value, unit)
            attributes = {"yield_mpa": str(int(si[0])) if si else str(int(value))}
            yield (
                SteelEntityType.STEEL_GRADE,
                f"Fy {attributes['yield_mpa']} MPa",
                CONFIDENCE_PATTERN,
                attributes,
                _occurrence(match, page, EntitySource.REGEX),
            )

    def _bolts(self, text: str, page: int | None) -> Iterator[Found]:
        for match in _BOLT.finditer(text):
            diameter, length, grade = match.group(1), match.group(2), match.group(3)
            # A bare "M20" with no grade and no length is as likely to be a
            # thread callout as a bolt spec; still recorded, but the grade is
            # what makes it a specification.
            canonical = canonical_bolt(diameter, grade, length)
            attributes: dict[str, str] = {"diameter_mm": str(int(diameter))}
            if length:
                attributes["length_mm"] = str(int(length))
            if grade:
                attributes["property_class"] = grade
                spec = self._gazetteer.bolt_grade(grade)
                if spec:
                    attributes.update({k: str(v) for k, v in spec.items()})
            yield (
                SteelEntityType.BOLT_SPEC,
                canonical,
                CONFIDENCE_GAZETTEER if grade else CONFIDENCE_PATTERN,
                attributes,
                _occurrence(match, page, EntitySource.REGEX),
            )

        for match in _HSFG.finditer(text):
            yield (
                SteelEntityType.BOLT_SPEC,
                match.group(0).upper().replace("-", ""),
                CONFIDENCE_PATTERN,
                {"type": "friction_grip"},
                _occurrence(match, page, EntitySource.REGEX),
            )

    def _welds(self, text: str, page: int | None) -> Iterator[Found]:
        for match in _WELD_SIZE.finditer(text):
            size, kind = match.group(1), match.group(2).upper()
            yield (
                SteelEntityType.WELD_SPEC,
                f"{int(size)}mm {kind} weld",
                CONFIDENCE_PATTERN,
                {"size_mm": str(int(size)), "weld_type": kind.lower()},
                _occurrence(match, page, EntitySource.REGEX),
            )

        for match in _WELD_SHORT.finditer(text):
            size = match.group(1)
            yield (
                SteelEntityType.WELD_SPEC,
                f"{int(size)}mm FILLET weld",
                CONFIDENCE_PATTERN,
                {"size_mm": str(int(size)), "weld_type": "fillet"},
                _occurrence(match, page, EntitySource.REGEX),
            )

        for match in _ELECTRODE.finditer(text):
            yield (
                SteelEntityType.MATERIAL_SPEC,
                match.group(0).replace(" ", "").upper(),
                CONFIDENCE_PATTERN,
                {"kind": "electrode"},
                _occurrence(match, page, EntitySource.REGEX),
            )

    def _dimensions(self, text: str, page: int | None) -> Iterator[Found]:
        for match in _DIMENSION.finditer(text):
            value = float(match.group(1))
            unit = canonical_unit(match.group(2))
            attributes = {"value": match.group(1), "unit": unit}
            si = to_si(value, unit)
            if si:
                attributes["value_si"], attributes["unit_si"] = str(si[0]), si[1]

            window = text[max(0, match.start() - _LOAD_WINDOW) : match.start()]
            is_load = unit in _LOAD_UNITS and _LOAD_CONTEXT.search(window) is not None

            yield (
                SteelEntityType.LOAD_CAPACITY if is_load else SteelEntityType.DIMENSION,
                f"{match.group(1)} {unit}",
                CONFIDENCE_CONTEXT if is_load else CONFIDENCE_PATTERN,
                attributes,
                _occurrence(match, page, EntitySource.REGEX),
            )

    def _identifiers(self, text: str, page: int | None) -> Iterator[Found]:
        for entity_type, pattern in _IDENTIFIER_PATTERNS.items():
            for match in pattern.finditer(text):
                yield (
                    entity_type,
                    canonical_identifier(match.group(1)),
                    CONFIDENCE_PATTERN,
                    {},
                    _occurrence(match, page, EntitySource.REGEX),
                )

    def _standards(self, text: str, page: int | None) -> Iterator[Found]:
        for match in _STANDARD_REF.finditer(text):
            body = match.group(1).upper()
            number = re.sub(r"\s+", "-", match.group(2).strip())
            yield (
                SteelEntityType.STANDARD_REF,
                f"{body} {number}",
                CONFIDENCE_PATTERN,
                {"body": body},
                _occurrence(match, page, EntitySource.REGEX),
            )


def _occurrence(match: re.Match[str], page: int | None, source: EntitySource) -> EntityOccurrence:
    return EntityOccurrence(
        raw=match.group(0).strip(),
        source=source,
        page_number=page,
        char_start=match.start(),
        char_end=match.end(),
    )


def _merge(found: Iterable[Found]) -> EntityExtractionResult:
    """Collapse repeated matches into one entity per (type, canonical).

    Occurrences are unioned rather than replaced, so a designation appearing
    on four sheets keeps all four provenance records -- which is what makes
    "which drawings reference PL-12" answerable from the entity index alone.
    """
    merged: dict[tuple[SteelEntityType, str], SteelEntity] = {}

    for entity_type, canonical, confidence, attributes, occurrence in found:
        key = (entity_type, canonical)
        existing = merged.get(key)
        if existing is None:
            merged[key] = SteelEntity(
                type=entity_type,
                canonical=canonical,
                confidence=confidence,
                occurrences=(occurrence,),
                attributes=dict(attributes),
            )
            continue

        merged[key] = SteelEntity(
            type=entity_type,
            canonical=canonical,
            confidence=max(existing.confidence, confidence),
            occurrences=(*existing.occurrences, occurrence),
            # Later attributes fill gaps but never overwrite: the first match
            # that carried a standard or a property class is as good as any.
            attributes={**attributes, **existing.attributes},
        )

    return EntityExtractionResult(
        entities=sorted(merged.values(), key=lambda e: (e.type.value, e.canonical))
    )
