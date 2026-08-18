"""What a vision fallback produces, and what it decided before producing it.

Vision never writes the answer. It returns an *observation* -- what a model
reports seeing in one crop of one drawing -- which then enters the ordinary
context as one more piece of evidence, ranked below everything the file
actually states. The generator writes the answer from that context, under the
same grounding guard as any other question.

The distinction is not stylistic. A vision model asked "what is the bolt
diameter" will answer, confidently, from pixels. The drawing says
`ALL BOLTS 3/4" DIA. A325`, and if those two disagree the file is right --
it is the thing being described, not a photograph of it. Keeping the
observation separate from the answer is what leaves room to say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.value_objects.provenance import Region

#: Bumped whenever the prompt or the expected schema changes. Part of the
#: cache key, because an observation produced by an older prompt answers a
#: subtly different question and must not be served for the new one.
PROMPT_VERSION = "v1"


class EscalationReason:
    """Why a question was, or was not, sent to a vision model.

    Recorded on every decision including the refusals, because "vision was not
    called" is the common case and the one an operator most often needs
    explained -- a silent no is indistinguishable from a broken fallback.
    """

    UNRESOLVED_SYMBOL = "unresolved_symbol"
    UNRESOLVED_ANNOTATION = "unresolved_annotation"
    LOW_CONFIDENCE_ASSOCIATION = "low_confidence_association"
    SCANNED_REGION = "scanned_region"

    NOT_A_VISUAL_QUESTION = "not_a_visual_question"
    DETERMINISTIC_EVIDENCE_SUFFICIENT = "deterministic_evidence_sufficient"
    NO_EVIDENCE_AT_ALL = "no_evidence_at_all"
    NO_REGION_TO_INSPECT = "no_region_to_inspect"
    DISABLED = "disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BLOCKED_BY_GOVERNANCE = "blocked_by_governance"
    BUDGET_EXHAUSTED = "budget_exhausted"

    #: The reasons that mean "go ahead".
    ESCALATING = frozenset(
        {
            UNRESOLVED_SYMBOL,
            UNRESOLVED_ANNOTATION,
            LOW_CONFIDENCE_ASSOCIATION,
            SCANNED_REGION,
        }
    )


@dataclass(frozen=True)
class RegionCandidate:
    """One place worth looking at, and the chunk that suggested it."""

    document_id: str
    document_name: str
    page_number: int
    region: Region
    #: The retrieved text that made this region a candidate, kept so the
    #: observation can be reported beside what the file already said.
    context: str = ""
    layers: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationDecision:
    """Whether to call vision, and why -- recorded either way."""

    escalate: bool
    reason: str
    detail: str = ""
    candidates: tuple[RegionCandidate, ...] = ()

    @classmethod
    def no(cls, reason: str, detail: str = "") -> EscalationDecision:
        return cls(escalate=False, reason=reason, detail=detail)

    @classmethod
    def yes(
        cls, reason: str, candidates: tuple[RegionCandidate, ...], detail: str = ""
    ) -> EscalationDecision:
        return cls(escalate=True, reason=reason, detail=detail, candidates=candidates)


@dataclass(frozen=True)
class VisionObservation:
    """What a model reports seeing in one crop. Not an answer.

    `uncertainties` is not decoration. A model asked what an unlabelled symbol
    represents will name one; the useful output is that name *plus* what it
    could not tell, because the second half is what stops the first being read
    as a measurement.
    """

    observation: str
    objects: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    confidence: float = 0.0
    region: Region | None = None
    document_id: str = ""
    document_name: str = ""
    model_id: str = ""
    #: True when this came from the cache rather than a fresh call.
    cached: bool = False

    @property
    def is_usable(self) -> bool:
        return bool(self.observation.strip())

    def as_evidence(self) -> str:
        """The observation as a context passage, labelled as what it is.

        Written so the generator cannot mistake it for something the drawing
        states. The wording is deliberate and load-bearing: an observation
        introduced as a fact would be quoted as one.
        """
        lines = [
            f"VISUAL OBSERVATION (not stated by the file; a vision model's reading "
            f"of a rendered crop of {self.document_name or 'the drawing'}, "
            f"confidence {self.confidence:.2f}): {self.observation.strip()}"
        ]
        if self.objects:
            lines.append("Objects reported: " + ", ".join(self.objects))
        if self.relationships:
            lines.append("Relationships reported: " + ", ".join(self.relationships))
        if self.uncertainties:
            lines.append("The model could not determine: " + ", ".join(self.uncertainties))
        lines.append(
            "This is weaker evidence than anything the CAD file states directly. "
            "Where it disagrees with an extracted dimension, annotation or "
            "schedule value, the extracted value is correct."
        )
        return "\n".join(lines)


@dataclass
class VisionBudget:
    """A hard ceiling on calls per question.

    Twenty retrieved chunks must never mean twenty vision calls, and the
    limit belongs here rather than in a caller's loop so that every path --
    retry, multi-region, a future re-ask -- shares one counter.
    """

    max_calls: int = 2
    spent: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def charge(self) -> None:
        self.spent += 1


@dataclass(frozen=True)
class VisionOutcome:
    """Everything one fallback attempt produced, for the trace and the answer."""

    decision: EscalationDecision
    observations: list[VisionObservation] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def used(self) -> bool:
        return bool(self.observations)
