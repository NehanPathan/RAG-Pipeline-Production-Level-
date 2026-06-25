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
