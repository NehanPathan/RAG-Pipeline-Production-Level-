from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IntentType(str, Enum):
    FACTUAL = "factual"
    POLICY_LOOKUP = "policy_lookup"
    ANALYTICAL = "analytical"
    COMPARISON = "comparison"
    SUMMARIZATION = "summarization"
    CONVERSATIONAL = "conversational"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass
class QueryIntent:
    type: IntentType
    confidence: float = 0.0
    domain: str = "general"
