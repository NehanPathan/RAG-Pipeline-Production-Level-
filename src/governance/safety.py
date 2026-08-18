"""Refusing a question at the door, without a model and without retrieval.

Two things are already true and worth stating, because they decide how much
this module needs to do.

**The output side is already guarded.** An answer is only produced from
retrieved passages, and the grounding policy refuses when nothing was
retrieved or nothing was cited. Ask this system how to build a weapon and it
finds nothing in a corpus of steel drawings and refuses. So this is not the
last line of defence; it is the *cheap* one, catching a request before it
costs a retrieval, a rerank, a compression pass and a generation.

**The domain is full of words that look alarming out of context.** A
structural steel corpus legitimately contains *blast* (shot blasting, blast
cleaning), *explosion-proof* (hazardous-area enclosures), *gun* (nail gun,
spray gun), *shear*, *ballistic*, *charge*, *impact* and *fire rating*. A
blocklist of nouns would refuse the drawings this system exists to read.

So every pattern here requires **instructional intent**, not a noun: a verb of
making or acquiring, aimed at an object. `how to make a bomb` matches;
`blast cleaning to SA 2.5` does not, and neither does `explosion-proof
junction box detail`. Where a category cannot be expressed that precisely it
is deliberately left out and left to the grounding guard, because a false
refusal on an engineering question is a failure the user cannot work around
and cannot understand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Verbs that turn a subject into a request for instructions. Kept separate
#: from the objects so each category reads as "how do I *do* X" rather than
#: "X was mentioned".
_INTENT = (
    r"(?:how\s+(?:do\s+i|to|can\s+i)|steps?\s+to|guide\s+to|instructions?\s+for"
    r"|teach\s+me\s+to|help\s+me\s+to|show\s+me\s+how\s+to|best\s+way\s+to"
    r"|give\s+me\s+(?:a\s+)?(?:recipe|method|procedure)\s+(?:for|to))"
)


@dataclass(frozen=True)
class SafetyRule:
    category: str
    pattern: re.Pattern[str]
    #: What the user is told. Specific enough to be actionable, short enough
    #: not to lecture.
    reply: str


def _rule(category: str, objects: str, reply: str) -> SafetyRule:
    return SafetyRule(
        category=category,
        pattern=re.compile(rf"{_INTENT}\s+.{{0,40}}?{objects}", re.IGNORECASE | re.DOTALL),
        reply=reply,
    )


_REFUSAL = (
    "I can't help with that. This assistant answers questions about the "
    "engineering documents indexed in this workspace."
)

RULES: tuple[SafetyRule, ...] = (
    _rule(
        "weapons",
        r"(?:bomb|explosive\s+device|ied\b|detonator|pipe\s+bomb|grenade"
        r"|silencer|suppressor\s+for\s+a\s+(?:gun|rifle|pistol)"
        r"|untraceable\s+(?:gun|firearm)|ghost\s+gun|automatic\s+weapon)",
        _REFUSAL,
    ),
    _rule(
        "drugs",
        r"(?:methamphetamine|meth\b|cocaine|heroin|fentanyl|mdma|lsd)",
        _REFUSAL,
    ),
    _rule(
        "intrusion",
        r"(?:ransomware|keylogger|malware|a\s+virus\s+to|botnet"
        r"|hack\s+(?:into|someone|a\s+(?:server|account|network))"
        r"|steal\s+(?:credentials|passwords)|bypass\s+authentication)",
        _REFUSAL,
    ),
    _rule(
        "self_harm",
        r"(?:kill\s+myself|end\s+my\s+life|commit\s+suicide|hurt\s+myself)",
        "I can't help with that. If you're struggling, please talk to someone "
        "you trust or contact a local support line. This assistant only "
        "answers questions about the documents in this workspace.",
    ),
    _rule(
        "violence",
        r"(?:kill|murder|poison|assault)\s+(?:someone|a\s+person|my\b|him\b|her\b|them\b)",
        _REFUSAL,
    ),
)

#: Sexual content involving minors, matched on the subject rather than on an
#: instruction verb -- the intent gate is right for "how do I make X" but
#: wrong here, where any request at all is refused.
_CSAM = re.compile(
    r"\b(?:child|children|minor|underage|teen|kid)\w*\s+(?:porn\w*|sexual|nude|naked)"
    r"|\b(?:porn\w*|sexual|nude)\w*\s+(?:child|children|minor|underage)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyVerdict:
    """Whether to answer, and why not."""

    allowed: bool
    category: str = ""
    reply: str = ""

    @property
    def refused(self) -> bool:
        return not self.allowed


ALLOWED = SafetyVerdict(allowed=True)


def screen_question(query: str) -> SafetyVerdict:
    """Whether a question may reach the pipeline at all.

    Returns `ALLOWED` for the overwhelming majority, including every question
    about a drawing however it is phrased. A refusal here costs nothing and
    saves a full pipeline run; a false refusal costs a user their answer, so
    the patterns are narrow by design and the grounding guard remains the
    thing that catches everything else.
    """
    text = (query or "").strip()
    if not text:
        return ALLOWED

    if _CSAM.search(text):
        return SafetyVerdict(allowed=False, category="csam", reply=_REFUSAL)

    for rule in RULES:
        if rule.pattern.search(text):
            return SafetyVerdict(allowed=False, category=rule.category, reply=rule.reply)

    return ALLOWED
