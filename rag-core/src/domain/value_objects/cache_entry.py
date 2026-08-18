from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SemanticCacheEntry:
    query_text: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    model_used: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def source_document_ids(self) -> list[str]:
        """Documents this cached answer was built from.

        Written into the cache payload so a deleted document can invalidate
        exactly the answers derived from it. Without this the cache is a
        deletion leak: the document is gone from Postgres, Qdrant and
        Elasticsearch, and the system keeps serving an answer built from it
        to anyone who asks a similar question.
        """
        ids = {
            c.get("document_id")
            for c in self.citations
            if isinstance(c, dict) and c.get("document_id")
        }
        return sorted(str(i) for i in ids)
