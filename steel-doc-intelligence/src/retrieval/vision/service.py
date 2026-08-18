"""The one object the query graph talks to.

Keeps the graph node short and keeps retrieval types out of the escalation
logic: `escalation.decide` works on plain tuples so it can be tested without
building a retrieval result, and the translation from `CompressedChunk` to
those tuples lives here.

Also where an accepted observation becomes a context passage. It is appended
rather than substituted, and its wording says what it is, because the whole
architecture rests on vision being *additional* evidence that ranks below the
file rather than a competing reading of it.
"""

from __future__ import annotations

from typing import Any

from src.domain.entities.document import ChunkMetadata, DocumentChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.monitoring.logger import get_logger
from src.retrieval.vision.escalation import decide
from src.retrieval.vision.fallback import VisionFallback
from src.retrieval.vision.models import VisionBudget, VisionOutcome

logger = get_logger(__name__)


class VisionFallbackService:
    """Decides, runs and reports one vision escalation per question."""

    def __init__(
        self,
        fallback: VisionFallback,
        *,
        enabled: bool = True,
        max_regions: int = 2,
        max_calls: int = 2,
    ) -> None:
        self._fallback = fallback
        self._enabled = enabled
        self._max_regions = max_regions
        self._max_calls = max_calls

    async def consider(
        self,
        query: str,
        chunks: list[Any],
        trace_id: str = "",
        principal: Any = None,
    ) -> VisionOutcome:
        passages = [_passage(item) for item in chunks]
        decision = decide(
            query,
            [p for p in passages if p is not None],
            enabled=self._enabled,
            max_regions=self._max_regions,
        )

        deterministic_text = "\n".join(p[2] for p in passages if p is not None)
        outcome = await self._fallback.run(
            query, decision, VisionBudget(max_calls=self._max_calls), deterministic_text
        )

        logger.info(
            "vision_decision",
            trace_id=trace_id,
            escalated=decision.escalate,
            reason=outcome.decision.reason,
            detail=outcome.decision.detail,
            observations=len(outcome.observations),
            conflicts=len(outcome.conflicts),
        )

        if outcome.used:
            # Auditable because a crop of a drawing left the deployment. The
            # record names the document and region, never the image.
            await audit_record(
                action=AuditAction.VISION_ESCALATED,
                actor_id=getattr(principal, "user_id", None),
                actor_role=getattr(principal, "role", None),
                resource_type="document",
                resource_id=outcome.observations[0].document_id,
                outcome=AuditOutcome.ALLOWED,
                reason=(
                    f"{outcome.decision.reason}: {len(outcome.observations)} region(s) "
                    f"sent to {outcome.observations[0].model_id}"
                ),
            )
        return outcome

    def as_chunks(self, outcome: VisionOutcome) -> list[CompressedChunk]:
        """Observations as context passages, ranked and labelled below the file.

        Built as ordinary `CompressedChunk`s so generation, citation building
        and the grounding guard treat them exactly like retrieved text -- an
        observation the guard could not refuse would be an observation that
        bypassed grounding. `rerank_score` is 0.0 and `final_rank` is last,
        so they sort below everything the file actually says.
        """
        out: list[CompressedChunk] = []
        for observation in outcome.observations:
            text = observation.as_evidence()
            if outcome.conflicts:
                text += "\n" + "\n".join(outcome.conflicts)
            chunk = DocumentChunk(
                document_id=_uuid_or_none(observation.document_id),
                content=text,
                position=10_000,
                document_name=observation.document_name,
                chunk_metadata=ChunkMetadata(
                    page_number=observation.region.page_number if observation.region else None,
                    regions=[observation.region] if observation.region else [],
                    region_precision="block" if observation.region else None,
                    content_kind="vision_observation",
                ),
            )
            out.append(
                CompressedChunk(
                    reranked=RerankedChunk(
                        fused=FusedChunk(chunk=chunk, rrf_score=0.0, rank=10_000),
                        rerank_score=0.0,
                        final_rank=10_000,
                    ),
                    compressed_content=text,
                    token_count=max(1, len(text) // 4),
                )
            )
        return out


def _passage(item: Any) -> tuple[str, str, str, int, list[Any], list[str]] | None:
    chunk = getattr(item, "chunk", None)
    if chunk is None:
        return None
    metadata = chunk.chunk_metadata
    text = getattr(item, "compressed_text", None) or chunk.content
    return (
        str(chunk.document_id),
        chunk.document_name or "",
        text,
        metadata.page_number or 1,
        list(metadata.regions),
        list(metadata.layers),
    )


def _uuid_or_none(value: str) -> Any:
    import uuid

    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return uuid.uuid4()
