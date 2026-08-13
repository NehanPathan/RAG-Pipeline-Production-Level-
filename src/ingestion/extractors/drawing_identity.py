"""Which drawing a document *is*, as opposed to which drawings it mentions.

A sheet names its own number in the title block and then names half a dozen
others in cross-references -- "SEE S-101 FOR SECTION", "REFER TO S-207". The
entity extractor finds all of them and cannot tell which is the subject,
because on a plotted PDF they look identical: both are a keyed drawing
number in a text run.

Getting this wrong is not a cosmetic error. The drawing number is the
identity a revision family is keyed on, so choosing a cross-referenced sheet
files this document as a revision of *that* sheet -- superseding a drawing it
has nothing to do with, and demoting the real current revision of it. That is
a wrong answer to "what is the current detail", delivered confidently, which
is the exact failure `RegisterRevision` exists to prevent.

So this fails closed. It answers only when the evidence is unambiguous, and
returns nothing otherwise, leaving `drawing_number` and `revision_label`
null. A null is visibly missing and can be set by hand; a confident wrong
answer is neither.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.extractors.models import EntityExtractionResult, SteelEntity, SteelEntityType
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


#: Key under `DocumentMetadata.custom_metadata` where the identity is stored.
#: Computed by the enricher, which already runs the extractor, and read by
#: the ingestion pipeline -- rather than re-deriving it from the persisted
#: entity dicts, which do not carry occurrences and so cannot reproduce the
#: repetition count this depends on.
CUSTOM_METADATA_KEY = "drawing_identity"


@dataclass(frozen=True)
class DrawingIdentity:
    drawing_number: str
    revision_label: str | None
    #: Why this number was chosen, for the ingestion log. A drawing register
    #: that silently picks between candidates is one nobody can audit.
    reason: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "drawing_number": self.drawing_number,
            "revision_label": self.revision_label,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> DrawingIdentity | None:
        if not isinstance(data, dict):
            return None
        number = data.get("drawing_number")
        if not isinstance(number, str) or not number:
            return None
        revision = data.get("revision_label")
        return cls(
            drawing_number=number,
            revision_label=revision if isinstance(revision, str) and revision else None,
            reason=str(data.get("reason") or ""),
        )


def extract_drawing_identity(result: EntityExtractionResult) -> DrawingIdentity | None:
    """The sheet's own drawing number and revision, or None if unclear.

    A drawing number wins by being repeated: a sheet's own number appears in
    the title block and usually again in the border or footer, while a
    cross-reference is mentioned once. When the top two are equally frequent
    there is no basis to choose, and choosing anyway would corrupt a revision
    family, so nothing is returned.
    """
    numbers = _of_type(result, SteelEntityType.DRAWING_NUMBER)
    if not numbers:
        return None

    ranked = sorted(numbers, key=lambda e: (e.count, e.confidence, e.canonical), reverse=True)
    best = ranked[0]

    if len(ranked) > 1:
        runner_up = ranked[1]
        if (best.count, best.confidence) == (runner_up.count, runner_up.confidence):
            # Two candidates with identical evidence. Picking either would be
            # a coin flip on the document's identity.
            logger.info(
                "drawing_identity_ambiguous",
                candidates=[e.canonical for e in ranked[:4]],
                count=best.count,
            )
            return None

    revision = _best_revision(result)
    return DrawingIdentity(
        drawing_number=best.canonical,
        revision_label=revision,
        reason=(
            f"{best.canonical} found {best.count}x at confidence {best.confidence:.2f}"
            + (f", revision {revision}" if revision else ", no revision found")
        ),
    )


def _best_revision(result: EntityExtractionResult) -> str | None:
    """The sheet's revision, when exactly one is present.

    A revision table lists every past revision, so several labels on one
    sheet is normal and says nothing about which is current -- the extractor
    sees `REV A`, `REV B` and `REV C` on a sheet that *is* Rev C. Ordering
    them here would duplicate `revision_sort_index` and guess besides, so
    multiple labels are left for a human.
    """
    revisions = _of_type(result, SteelEntityType.REVISION)
    if len(revisions) != 1:
        if revisions:
            logger.info("revision_ambiguous", candidates=sorted(e.canonical for e in revisions))
        return None
    return revisions[0].canonical


def _of_type(result: EntityExtractionResult, kind: SteelEntityType) -> list[SteelEntity]:
    return [e for e in result.entities if e.type is kind]
