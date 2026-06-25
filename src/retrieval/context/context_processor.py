from __future__ import annotations

import uuid

from src.domain.entities.conversation import Citation
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import RerankedChunk
from src.monitoring.stage_tracer import traced_stage
from src.retrieval.context.citation_preserver import CitationPreserver
from src.retrieval.context.context_compressor import ContextCompressor
from src.retrieval.context.context_deduplicator import ContextDeduplicator
from src.retrieval.context.token_budget_manager import TokenBudgetManager


class ContextProcessor:
    """Orchestrates Module E as one traced stage:
    dedup (original content) -> compress (small LLM) -> token budget
    (final guard, on compressed content) -> citation index assignment.

    Dedup runs before compression because containment checks need the
    original wording — an LLM-compressed excerpt may no longer be a literal
    substring of another chunk's content even if the originals were.
    """

    def __init__(
        self,
        deduplicator: ContextDeduplicator,
        compressor: ContextCompressor,
        budget_manager: TokenBudgetManager,
        citation_preserver: CitationPreserver,
    ) -> None:
        self._deduplicator = deduplicator
        self._compressor = compressor
        self._budget_manager = budget_manager
        self._citation_preserver = citation_preserver

    async def process(
        self, query: str, chunks: list[RerankedChunk]
    ) -> tuple[list[CompressedChunk], dict[uuid.UUID, Citation]]:
        async with traced_stage("context_processing", query=query, candidates=len(chunks)) as stage:
            deduped = self._deduplicator.deduplicate(chunks)
            compressed = await self._compressor.compress(query, deduped)
            selected = self._budget_manager.select(compressed)
            citations = self._citation_preserver.build(selected)
            stage.set_result(
                deduped_count=len(deduped),
                compressed_count=len(compressed),
                final_count=len(selected),
            )
            return selected, citations
