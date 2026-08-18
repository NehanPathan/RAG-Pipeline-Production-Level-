from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from src.domain.entities.conversation import Citation
from src.domain.entities.document import DocumentChunk
from src.domain.value_objects.retrieval_candidate import RerankedChunk


@dataclass
class CompressedChunk:
    """Output of Context Compression (Module E): a RerankedChunk whose
    content has been reduced by a small LLM to just the portion relevant to
    the query, plus the resulting token count once selected by
    TokenBudgetManager.
    """

    reranked: RerankedChunk
    compressed_content: str
    token_count: int = 0

    @property
    def chunk(self) -> DocumentChunk:
        return self.reranked.chunk


@dataclass
class AssembledContext:
    """Output of Context Assembly (Module G): the final formatted context
    string (with inline [n] citation markers) handed to PromptBuilder, plus
    the citations it references for downstream validation.
    """

    formatted_text: str
    total_tokens: int
    citations: dict[uuid.UUID, Citation] = field(default_factory=dict)
