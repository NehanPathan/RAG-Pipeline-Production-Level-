"""Deciding whether a question genuinely needs a picture.

Almost every question about a drawing does not. The deterministic pipeline
already reads the text, follows the leaders, groups the blocks, recovers the
schedules and knows which layer everything sits on, and a vision call on top
of that is cost without information -- and worse, a second opinion that can
disagree with the file.

So this module's job is mostly to say no, and to say *why*. Four failures
look alike from the outside and only one of them warrants a picture:

1. **Extraction failure** -- the file holds the answer and we did not read
   it. The fix is the extractor. Vision would paper over a bug.
2. **Indexing failure** -- we read it and did not index it. Same.
3. **Retrieval failure** -- indexed, not retrieved. Same again.
4. **Genuinely visual information** -- the file contains a mark with no
   semantic content: an unlabelled symbol, a hatch pattern, a detail whose
   meaning is carried by its shape. Nothing in the DXF says what it is,
   because a DXF does not record what a symbol *means*.

Only the fourth is a vision question, and the signal separating it from the
others is specific: the deterministic pipeline must have **looked and said so**.
An unresolved annotation, a low-confidence association, a region we could not
name -- those are the pipeline reporting a gap it can see the shape of. That
is very different from retrieval coming back empty, which means either a bug
or that the drawing simply does not say.

Retrieval coming back empty therefore does *not* escalate. If a drawing has
no load capacity on it, the answer is that the drawing does not say so -- and
a vision model asked to find one will find something.
"""

from __future__ import annotations

import re

from src.domain.value_objects.provenance import Region
from src.retrieval.vision.models import (
    EscalationDecision,
    EscalationReason,
    RegionCandidate,
)

#: Questions that are about appearance rather than about a stated value.
#: Deliberately narrow. "What is the bolt diameter" is not on this list and
#: must never be: the drawing states it, and a question that the file answers
#: in words is not a vision question however it is phrased.
_VISUAL_QUESTION = re.compile(
    r"\b("
    r"symbol|symbols|icon|hatch|hatching|shading|pattern|marking|markings"
    r"|what\s+(does|do)\s+(this|that|the)\s+\w*\s*(symbol|marking|shape|graphic)"
    r"|unlabell?ed|unlabelled|unmarked|unnamed|not\s+labell?ed"
    r"|look\s+like|looks\s+like|appears?\s+to\s+be|shown\s+(in|inside|at)"
    r"|depicted|drawn\s+(as|at)|graphic|pictogram|shape\s+of"
    r"|highlighted\s+(region|area|connection|detail)"
    r")\b",
    re.IGNORECASE,
)

#: Text in a retrieved passage saying the deterministic pipeline looked and
#: could not name the thing. These are the phrases the earlier phases emit --
#: see `dxf_loader._annotation_blocks_for` and `blocks.SymbolGroup.summary`.
_UNRESOLVED_MARKERS = (
    "could not be determined",
    "no leader reaches this text",
    "no geometry sits within reach",
    "unnamed shape",
    "not stated by the file",
)

#: A passage containing one of these is the file answering in words, so there
#: is nothing for a picture to add.
_RESOLVED_MARKERS = (
    "labels a",
    "connected to it by a leader",
    "so this is what the annotation refers to",
)


def looks_visual(query: str) -> bool:
    """Whether the question is about appearance rather than a stated value."""
    return bool(_VISUAL_QUESTION.search(query or ""))


def _mentions_unresolved(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _UNRESOLVED_MARKERS)


def _mentions_resolved(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RESOLVED_MARKERS)


def decide(
    query: str,
    passages: list[tuple[str, str, str, int, list[Region], list[str]]],
    *,
    enabled: bool = True,
    max_regions: int = 2,
) -> EscalationDecision:
    """Whether to escalate, given the question and what retrieval returned.

    `passages` are `(document_id, document_name, text, page, regions, layers)`
    tuples in rank order -- deliberately plain data rather than chunk objects,
    so this decision is testable without constructing a retrieval result.
    """
    if not enabled:
        return EscalationDecision.no(EscalationReason.DISABLED)

    if not looks_visual(query):
        return EscalationDecision.no(
            EscalationReason.NOT_A_VISUAL_QUESTION,
            "the question asks for a stated value, which the file either contains or does not",
        )

    if not passages:
        # The important refusal. Nothing retrieved means either a bug in the
        # pipeline or a drawing that does not carry the answer, and a picture
        # cannot tell those apart -- it can only produce something.
        return EscalationDecision.no(
            EscalationReason.NO_EVIDENCE_AT_ALL,
            "retrieval returned nothing; a vision call here would be guessing rather than reading",
        )

    unresolved = [p for p in passages if _mentions_unresolved(p[2])]
    if not unresolved:
        if any(_mentions_resolved(p[2]) for p in passages):
            return EscalationDecision.no(
                EscalationReason.DETERMINISTIC_EVIDENCE_SUFFICIENT,
                "the drawing states this and the extraction resolved it",
            )
        return EscalationDecision.no(
            EscalationReason.DETERMINISTIC_EVIDENCE_SUFFICIENT,
            "retrieved passages report no gap for a picture to fill",
        )

    candidates: list[RegionCandidate] = []
    for document_id, document_name, text, page, regions, layers in unresolved:
        for region in regions:
            candidates.append(
                RegionCandidate(
                    document_id=document_id,
                    document_name=document_name,
                    page_number=region.page_number or page,
                    region=region,
                    context=text,
                    layers=tuple(layers),
                )
            )
            if len(candidates) >= max_regions:
                break
        if len(candidates) >= max_regions:
            break

    if not candidates:
        # The pipeline reported a gap but cannot say where to look. Rendering
        # the whole sheet instead would be the "send the drawing to a model"
        # behaviour this design exists to avoid.
        return EscalationDecision.no(
            EscalationReason.NO_REGION_TO_INSPECT,
            "the unresolved passage carries no region to crop",
        )

    return EscalationDecision.yes(
        EscalationReason.UNRESOLVED_SYMBOL
        if "symbol" in query.lower()
        else EscalationReason.UNRESOLVED_ANNOTATION,
        candidates=tuple(candidates),
        detail=f"{len(candidates)} region(s) the extraction could not name",
    )
