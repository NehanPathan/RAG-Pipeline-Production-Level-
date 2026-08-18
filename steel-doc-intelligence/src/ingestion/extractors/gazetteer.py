"""Loads the steel section gazetteer.

The gazetteer is what turns a loose pattern match into a validated one. The
section-designation regex has to be permissive -- real designations range
from `ISMB 300` to `W14x90` to `L 75x75x6` -- and a permissive pattern over
engineering prose will happily read "spans W 12 metres" as an AISC wide
flange. Requiring the family to be a real one from a real standard is what
separates the two, and it is also what lets an extracted entity carry the
standard it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.monitoring.logger import get_logger

logger = get_logger(__name__)

DEFAULT_GAZETTEER_PATH = Path(__file__).parent / "data" / "steel_sections.yaml"


@dataclass(frozen=True)
class SectionFamily:
    prefix: str
    standard: str
    kind: str
    description: str = ""
    sizes: frozenset[int] = field(default_factory=frozenset)

    def knows_size(self, size: int) -> bool:
        """Whether this size appears in the family's published range.

        A family with no published sizes listed (the AISC families, whose
        designations are in inches) answers False for everything, so callers
        must treat "unknown size" as "not a reason to reject" rather than as
        a negative signal.
        """
        return size in self.sizes


@dataclass(frozen=True)
class Gazetteer:
    families: dict[str, SectionFamily]
    grades: dict[str, dict[str, Any]]
    bolt_grades: dict[str, dict[str, Any]]

    def family(self, prefix: str) -> SectionFamily | None:
        return self.families.get(prefix.upper())

    def has_family(self, prefix: str) -> bool:
        return prefix.upper() in self.families

    def grade(self, canonical: str) -> dict[str, Any] | None:
        return self.grades.get(canonical)

    def bolt_grade(self, canonical: str) -> dict[str, Any] | None:
        return self.bolt_grades.get(canonical)

    @property
    def family_prefixes(self) -> list[str]:
        """Prefixes longest-first.

        Order matters when these are alternated into a regex: `ISMB` must be
        tried before `IS`, and `HEB` before `HE`, or the shorter prefix wins
        and swallows the rest of the designation as a size.
        """
        return sorted(self.families, key=len, reverse=True)


def load_gazetteer(path: Path | str | None = None) -> Gazetteer:
    source = Path(path) if path else DEFAULT_GAZETTEER_PATH
    raw: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8")) or {}

    families: dict[str, SectionFamily] = {}
    for prefix, spec in (raw.get("families") or {}).items():
        families[prefix.upper()] = SectionFamily(
            prefix=prefix.upper(),
            standard=str(spec.get("standard", "")),
            kind=str(spec.get("kind", "")),
            description=str(spec.get("description", "")),
            sizes=frozenset(int(s) for s in (spec.get("sizes") or [])),
        )

    gazetteer = Gazetteer(
        families=families,
        grades={str(k): dict(v) for k, v in (raw.get("grades") or {}).items()},
        bolt_grades={str(k): dict(v) for k, v in (raw.get("bolt_grades") or {}).items()},
    )
    logger.info(
        "steel_gazetteer_loaded",
        path=str(source),
        families=len(gazetteer.families),
        grades=len(gazetteer.grades),
    )
    return gazetteer


@lru_cache(maxsize=1)
def get_gazetteer() -> Gazetteer:
    """Process-wide gazetteer, parsed once."""
    return load_gazetteer()
