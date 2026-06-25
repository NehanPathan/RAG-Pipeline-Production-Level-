from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from src.domain.repositories.search_repository import BM25SearchFilter
from src.domain.repositories.vector_repository import VectorSearchFilter
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MetadataFilterSpec:
    """Superset filter shape produced by the Query Intelligence Layer.

    Adapts onto the narrower, repository-specific VectorSearchFilter /
    BM25SearchFilter dataclasses so the existing Qdrant/ES repositories stay
    untouched. `date_from`/`date_to`/`custom` are captured for transparency
    (e.g. surfaced in /retrieval/inspect) but neither backend currently
    indexes a date field on chunks, so the adapters drop them with a logged
    warning rather than silently failing or raising.
    """

    user_id: uuid.UUID | None = None
    domain: str | None = None
    tags: list[str] | None = None
    file_type: str | None = None
    document_ids: list[uuid.UUID] | None = None
    date_from: date | None = None
    date_to: date | None = None
    custom: dict[str, str] = field(default_factory=dict)

    def to_vector_filter(self) -> VectorSearchFilter:
        self._warn_unsupported()
        return VectorSearchFilter(
            user_id=self.user_id,
            domain=self.domain,
            tags=self.tags,
            file_type=self.file_type,
            document_ids=self.document_ids,
        )

    def to_bm25_filter(self) -> BM25SearchFilter:
        self._warn_unsupported()
        return BM25SearchFilter(
            user_id=self.user_id,
            domain=self.domain,
            tags=self.tags,
            file_type=self.file_type,
            document_ids=self.document_ids,
        )

    def _warn_unsupported(self) -> None:
        if self.date_from or self.date_to or self.custom:
            logger.warning(
                "metadata_filter_unsupported_fields_dropped",
                date_from=str(self.date_from) if self.date_from else None,
                date_to=str(self.date_to) if self.date_to else None,
                custom_keys=list(self.custom.keys()),
            )
