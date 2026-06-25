from __future__ import annotations

import re
import uuid

from src.domain.entities.conversation import Citation
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


class CitationValidator:
    """Extracts which [n] citation markers actually appear in the generated
    answer and returns only the Citations that are both genuinely
    referenced AND valid (the index corresponds to a chunk that was really
    in the assembled context) -- catches the LLM citing a number that was
    never provided (a hallucinated source) and silently drops citations
    that were available but never referenced in the final answer.
    """

    def validate(self, answer: str, citations: dict[uuid.UUID, Citation]) -> list[Citation]:
        by_index = {citation.index: citation for citation in citations.values()}
        referenced_indices = sorted({int(match) for match in CITATION_PATTERN.findall(answer)})

        validated = []
        for index in referenced_indices:
            citation = by_index.get(index)
            if citation is None:
                logger.warning("citation_validation_hallucinated_index", index=index)
                continue
            validated.append(citation)
        return validated
