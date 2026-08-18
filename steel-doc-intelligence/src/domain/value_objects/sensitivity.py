from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class Sensitivity(str, Enum):
    """Data classification applied to a document and inherited by its chunks.

    Lives in the domain layer, not the governance layer: the *classification*
    of a document is a property of the data itself, while the rules about who
    holds which clearance are policy (see src/governance/policy.py, which
    re-exports this type so governance call sites read naturally).

    Ordered least-to-most restrictive. A principal may read a chunk only when
    the chunk's level is <= the principal's clearance level, which is what
    makes this usable as a retrieval-time filter rather than an answer-time
    redaction — the model never sees what the caller may not see.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def level(self) -> int:
        return _LEVELS[self]

    def readable_with(self, clearance: Sensitivity) -> bool:
        return self.level <= clearance.level

    @classmethod
    def parse(cls, raw: str | None, default: Sensitivity) -> Sensitivity:
        """Never raises — unknown or missing values fall back to `default`.

        Chunks indexed before this field existed carry no `sensitivity` in
        their Qdrant/Elasticsearch payload. Resolving those to the configured
        baseline (`internal`) rather than to PUBLIC is the safe direction: an
        unlabelled document is not a public one.
        """
        if raw is None or raw == "":
            return default
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            logger.warning("sensitivity_unrecognised", value=str(raw)[:64], fallback=default.value)
            return default

    @classmethod
    def at_or_below(cls, clearance: Sensitivity) -> list[Sensitivity]:
        """The full allow-list for a clearance, for building backend filters.

        Backends filter with "value in [...]" rather than a range comparison,
        because neither Qdrant payload indexes nor Elasticsearch keyword
        fields order string values the way this enum does.
        """
        return [s for s in cls if s.level <= clearance.level]

    @classmethod
    def values_at_or_below(cls, clearance: Sensitivity) -> list[str]:
        return [s.value for s in cls.at_or_below(clearance)]


_LEVELS: Mapping[Sensitivity, int] = MappingProxyType(
    {
        Sensitivity.PUBLIC: 0,
        Sensitivity.INTERNAL: 1,
        Sensitivity.CONFIDENTIAL: 2,
        Sensitivity.RESTRICTED: 3,
    }
)
