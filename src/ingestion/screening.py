"""Deciding whether a document belongs in this corpus, before it is indexed.

Two questions, both answered from signals the pipeline already computes, and
neither answered by a model. Running an LLM over every uploaded file to ask
"is this a steel document" would cost a call per upload to replace a judgement
the extraction already makes for free: a structural drawing is dense with
section designations, grades, bolt specs and drawing numbers, and a document
containing none of those in thousands of words is very probably not one.

**Is it relevant?** Scored from steel entities found by the existing regex
extractor, engineering vocabulary, and what the parser decided the content
*is* -- a file the CAD reader parsed as `cad_native` is a drawing whatever its
words say.

**Is it safe?** A small, high-precision list of terms that would never appear
in a construction document. Deliberately not a general profanity filter: site
correspondence contains swearing, and refusing a legitimate RFI over language
would be a worse failure than accepting an odd file.

**Nothing is deleted, and nothing is silently dropped.** A file that fails is
quarantined -- stored, marked, kept out of retrieval, with the reason and the
scores recorded so a steward can look and disagree. That asymmetry is
deliberate and is the same one the drawn-schedule detector makes in reverse: a
fabricated table is worse than a missing one, and here a legitimate drawing
turned away at the door is worse than an irrelevant file sitting quarantined.
A reviewer can release a quarantined file in a moment; they cannot recover an
upload the system refused and the user gave up on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.monitoring.logger import get_logger

logger = get_logger(__name__)

#: Vocabulary that says "construction document" without needing an entity
#: match. Broad on purpose -- this is a relevance signal, not a classifier,
#: and a specification, an RFI and a drawing should all clear it.
_DOMAIN_TERMS = (
    "drawing",
    "detail",
    "section",
    "elevation",
    "plan",
    "revision",
    "specification",
    "schedule",
    "beam",
    "column",
    "plate",
    "bolt",
    "weld",
    "steel",
    "concrete",
    "structural",
    "connection",
    "foundation",
    "girder",
    "purlin",
    "truss",
    "anchor",
    "grade",
    "tolerance",
    "assembly",
    "fabrication",
    "erection",
    "load",
    "moment",
    "shear",
    "reinforcement",
    "contractor",
    "engineer",
    "project",
    "sheet",
    "scale",
    "dimension",
    "material",
)

_DOMAIN_RE = re.compile(r"\b(" + "|".join(_DOMAIN_TERMS) + r")\w*\b", re.IGNORECASE)

#: Terms with no legitimate reading in a construction document. Kept short and
#: unambiguous: every entry here is a word whose presence in a drawing set or
#: a specification would itself be the anomaly. A longer list would be a
#: profanity filter, which is a different and worse control -- site
#: correspondence swears, and that is not a reason to quarantine an RFI.
_EXPLICIT_TERMS = (
    r"pornograph\w*",
    r"\bxxx\b",
    r"\bhardcore\s+sex\b",
    r"sexually\s+explicit",
    r"\bescort\s+service\b",
    r"child\s+(?:porn\w*|abuse\s+material)",
)
_EXPLICIT_RE = re.compile("|".join(_EXPLICIT_TERMS), re.IGNORECASE)

#: Content kinds the CAD/drawing path produced. A file the parser read as a
#: drawing is a drawing, whatever its text density says -- a detail sheet can
#: carry fewer than fifty words.
_DRAWING_KINDS = frozenset({"cad_native", "vector_drawing", "scanned_drawing", "mixed"})

#: Below this, a document is quarantined as out of scope. Set low on purpose.
#: The cost of being wrong is asymmetric: a false quarantine blocks real work
#: and a false pass merely leaves an odd file in the corpus, where the
#: retrieval filters and the grounding guard already stop it being quoted as
#: an authority on anything.
DEFAULT_MIN_RELEVANCE = 0.15

#: Documents shorter than this are not scored on vocabulary at all. A title
#: sheet or a transmittal can be a dozen words, and a ratio computed over
#: twelve tokens is noise.
_MIN_WORDS_FOR_RATIO = 120


@dataclass(frozen=True)
class ScreeningVerdict:
    """Whether a document may be indexed, and the evidence either way."""

    allowed: bool
    relevance: float
    category: str = ""
    reason: str = ""
    signals: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def quarantined(self) -> bool:
        return not self.allowed


def screen_document(
    text: str,
    *,
    content_kind: str | None = None,
    entity_count: int = 0,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
) -> ScreeningVerdict:
    """Score a parsed document for relevance and screen it for explicit content.

    `entity_count` comes from the steel entity extractor the pipeline already
    runs. Passing it in rather than re-extracting here keeps this function
    pure and cheap enough to unit-test without a gazetteer.
    """
    body = text or ""
    words = len(body.split())

    explicit = _EXPLICIT_RE.search(body)
    if explicit is not None:
        # Reported by category, never by quoting the match: an audit record
        # that repeats the offending text has republished it.
        logger.warning("document_screening_explicit", term_category="explicit")
        return ScreeningVerdict(
            allowed=False,
            relevance=0.0,
            category="explicit",
            reason=(
                "The document contains explicit content that does not belong in an "
                "engineering corpus. It has been quarantined for review rather than "
                "indexed."
            ),
            signals={"words": words},
        )

    kind = (content_kind or "").lower()
    if kind in _DRAWING_KINDS:
        # The parser recognised a drawing. That is a stronger statement about
        # what the file is than any word count, and a detail sheet legitimately
        # carries almost no prose.
        return ScreeningVerdict(
            allowed=True,
            relevance=1.0,
            reason=f"parsed as {kind}",
            signals={"content_kind": kind, "words": words, "entities": entity_count},
        )

    domain_hits = len(_DOMAIN_RE.findall(body))
    if words < _MIN_WORDS_FOR_RATIO:
        # Too short to judge, so it is not judged. A transmittal, a title
        # sheet or a covering note is a handful of words, and a relevance
        # ratio over eighty tokens is noise -- quarantining on it would refuse
        # ordinary paperwork with a number that means nothing. "Cannot tell"
        # has to resolve to *allow*: the explicit screen above still applies at
        # any length, and everything downstream (clearance filters, the
        # grounding guard) still governs what a short file can be quoted for.
        return ScreeningVerdict(
            allowed=True,
            relevance=1.0,
            reason=f"too short to screen on relevance ({words} words)",
            signals={"words": words, "domain_hits": domain_hits, "entities": entity_count},
        )

    # Domain terms per hundred words, saturating at 2% -- a real
    # specification runs far above that, and the point is to separate
    # "engineering document" from "not one", not to rank the good ones.
    density = domain_hits / max(words, 1)
    relevance = min(1.0, density / 0.02)
    if entity_count:
        # An exact section designation or bolt spec is worth more than loose
        # vocabulary: `ISMB 300` is not a word that turns up by accident.
        relevance = min(1.0, relevance + 0.25 * min(entity_count, 4))

    signals: dict[str, float | int | str] = {
        "words": words,
        "domain_hits": domain_hits,
        "entities": entity_count,
        "content_kind": kind or "unknown",
        "relevance": round(relevance, 3),
    }

    if relevance < min_relevance:
        logger.info("document_screening_irrelevant", **signals)
        return ScreeningVerdict(
            allowed=False,
            relevance=relevance,
            category="out_of_scope",
            reason=(
                f"The document shows no engineering content: {domain_hits} domain "
                f"term(s) and {entity_count} steel designation(s) across {words} words. "
                "It has been quarantined for review rather than indexed."
            ),
            signals=signals,
        )

    return ScreeningVerdict(allowed=True, relevance=relevance, reason="relevant", signals=signals)
