"""Domain entities extracted from steel structure documents.

The shape is driven by one requirement: a reviewer must be able to ask of
any extracted fact "where did this come from, and how confident are we".
So an entity carries every place it was seen, each with its own source, and
confidence is explicit rather than implied by which pass produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.ingestion.loaders.base import BoundingBox


class EntitySource(str, Enum):
    """Which pass produced an occurrence.

    Recorded per occurrence rather than per entity because the same
    designation is often found by the regex pass on one page and only by the
    LLM on another, and dropping that distinction would make a low-confidence
    reading indistinguishable from a validated one.
    """

    REGEX = "regex"
    GAZETTEER = "gazetteer"
    LLM = "llm"
    VISION = "vision"
    CAD = "cad"


class SteelEntityType(str, Enum):
    SECTION_DESIGNATION = "section_designation"
    STEEL_GRADE = "steel_grade"
    BOLT_SPEC = "bolt_spec"
    WELD_SPEC = "weld_spec"
    DIMENSION = "dimension"
    LOAD_CAPACITY = "load_capacity"
    DRAWING_NUMBER = "drawing_number"
    PART_MARK = "part_mark"
    REVISION = "revision"
    PROJECT_NUMBER = "project_number"
    STANDARD_REF = "standard_ref"
    MATERIAL_SPEC = "material_spec"


@dataclass(frozen=True)
class EntityOccurrence:
    """One place an entity was seen.

    `char_start`/`char_end` are offsets into the text that was scanned, so
    they only mean anything relative to that text -- which is why chunk-level
    extraction re-runs over the chunk rather than trying to translate
    document offsets.
    """

    raw: str
    source: EntitySource
    page_number: int | None = None
    bbox: BoundingBox | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True)
class SteelEntity:
    """A canonicalised fact, with every place it was found.

    `canonical` is the form used for indexing and matching: `ISMB300`,
    `ISMB 300` and `I.S.M.B.-300` all reduce to `ISMB 300`, which is what
    makes them retrieve the same chunk.

    `attributes` carries whatever the type warrants -- the standard a
    designation belongs to, an SI-normalised value and unit for a dimension,
    a bolt class's tensile strength. Kept as a flat string map so it survives
    a JSONB round-trip without a schema migration per attribute.
    """

    type: SteelEntityType
    canonical: str
    confidence: float
    occurrences: tuple[EntityOccurrence, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.occurrences)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type.value,
            "canonical": self.canonical,
            "confidence": round(self.confidence, 3),
            "count": self.count,
            "attributes": dict(self.attributes),
            "pages": sorted(
                {o.page_number for o in self.occurrences if o.page_number is not None}
            ),
            "sources": sorted({o.source.value for o in self.occurrences}),
        }


@dataclass
class EntityExtractionResult:
    entities: list[SteelEntity] = field(default_factory=list)

    @property
    def by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entity in self.entities:
            counts[entity.type.value] = counts.get(entity.type.value, 0) + 1
        return counts

    def canonicals(self, *types: SteelEntityType) -> list[str]:
        """Canonical forms, optionally filtered by type.

        This is what gets denormalised onto a chunk as `entity_canonicals`
        and appended to the document's tags -- both already indexed as
        keyword fields in Qdrant and Elasticsearch, so a filterable steel
        index costs no new index schema.
        """
        wanted = set(types)
        return sorted(
            {e.canonical for e in self.entities if not wanted or e.type in wanted}
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "by_type": self.by_type,
        }
