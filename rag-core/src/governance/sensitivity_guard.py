from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypeVar

from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.sensitivity import Sensitivity
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import retrieval_blocked_chunks

logger = get_logger(__name__)


class _HasChunk(Protocol):
    chunk: DocumentChunk


T = TypeVar("T", bound=_HasChunk)


class SensitivityGuard:
    """Drops retrieved chunks the principal is not cleared to read.

    This is the *second* of two enforcement points, and it exists precisely
    because the first one can be incomplete:

      1. Pre-filter (primary) — VectorSearchFilter/BM25SearchFilter push the
         allow-list into Qdrant and Elasticsearch, so unreadable chunks never
         enter the candidate set and cannot consume top_k slots.
      2. Post-filter (this class) — re-checks whatever came back.

    Relying on the pre-filter alone would be fragile in three concrete ways:
    a chunk indexed before the `sensitivity` field existed carries no value
    for the backend to match on; a future retrieval path could be added that
    forgets to pass the filter; and the two backends express the "missing
    field" case differently, so a mismatch between them would leak through
    whichever is more permissive. A cheap in-process comparison closes all
    three, and anything it drops is a defect worth alerting on — hence the
    counter rather than a silent filter.
    """

    def __init__(self, default_sensitivity: Sensitivity = Sensitivity.INTERNAL) -> None:
        self._default = default_sensitivity

    def filter(self, results: Iterable[T], clearance: Sensitivity) -> tuple[list[T], int]:
        """Return (permitted results, number withheld)."""
        permitted: list[T] = []
        blocked = 0

        for result in results:
            sensitivity = getattr(result.chunk, "sensitivity", None) or self._default
            if sensitivity.readable_with(clearance):
                permitted.append(result)
                continue
            blocked += 1
            retrieval_blocked_chunks.labels(sensitivity=sensitivity.value).inc()

        if blocked:
            # Warning, not info: with the pre-filter working correctly this
            # count is zero. A non-zero value means one of the three failure
            # modes above is live and should be investigated.
            logger.warning(
                "retrieval_chunks_blocked_by_clearance",
                blocked=blocked,
                clearance=clearance.value,
            )
        return permitted, blocked
