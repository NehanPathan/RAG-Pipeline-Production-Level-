from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from src.routing.routes import Route, RouteDecision, RouteSource
from src.tools.builtin.calculator import looks_arithmetic
from src.tools.builtin.translation import parse as parse_translation

# Whole-utterance greetings only. `\b(hi)\b` would match "hi" inside "which
# hire policy applies" -- the anchors are what stop a greeting rule from
# hijacking a real question.
_GREETING_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|hiya|howdy|good\s+(morning|afternoon|evening)|"
    r"how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|sup)"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^\s*(thanks|thank\s+you|ty|cheers|appreciate\s+it|got\s+it|ok(ay)?|cool|nice|"
    r"bye|goodbye|see\s+you|good\s?night)[\s!.,?]*$",
    re.IGNORECASE,
)

_IDENTITY_RE = re.compile(
    r"^\s*(who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|help|"
    r"what\s+do\s+you\s+do|how\s+do\s+you\s+work)[\s!.,?]*$",
    re.IGNORECASE,
)

# Covers the four shapes people actually use: "what is the date",
# "what time is it" (noun before the verb), "today's date", and bare "time".
# The trailing `$` is what keeps it from matching "what is the effective date
# of the contract", which is a document question.
_DATETIME_RE = re.compile(
    r"^\s*"
    r"(?:what(?:'?s| is)?\s+)?"
    r"(?:the\s+)?"
    r"(?:current\s+|today'?s?\s+)?"
    r"(?:date|time|day(?:\s+of\s+the\s+week)?|month|year)"
    r"(?:\s+(?:is\s+it|now|today|right\s+now))?"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)

# Signals the answer needs information newer than the indexed corpus.
_WEB_RE = re.compile(
    r"\b(latest|current|today'?s|breaking|recent|news|stock\s+price|"
    r"weather|who\s+won|right\s+now|as\s+of\s+today|this\s+week)\b",
    re.IGNORECASE,
)

# Signals the question is about the indexed documents. Checked *before* the
# web rule, because "what does our latest policy say" contains "latest" but
# is unambiguously a document question.
_DOCUMENT_RE = re.compile(
    r"\b(policy|policies|document|documents|handbook|contract|agreement|clause|"
    r"section|procedure|guideline|manual|report|spec|specification|sop|"
    r"according\s+to|in\s+the\s+(doc|file|pdf)|our\s+|company|internal)\b",
    re.IGNORECASE,
)

GREETING_REPLY = (
    "Hello. I answer questions from the documents indexed in this workspace — "
    "policies, procedures, contracts and reports. What would you like to know?"
)

THANKS_REPLY = "You're welcome. Ask me anything else about the indexed documents."

IDENTITY_REPLY = (
    "I'm a retrieval-augmented assistant for this workspace's document corpus. "
    "Ask a question and I'll find the relevant passages and answer from them, "
    "citing the sources. I can also handle arithmetic and translation directly."
)


@dataclass(frozen=True)
class Rule:
    name: str
    matches: Callable[[str], bool]
    decide: Callable[[str], RouteDecision]


def _decision(route: Route, reason: str, **args) -> RouteDecision:
    return RouteDecision(
        route=route, source=RouteSource.RULE, confidence=1.0, reason=reason, args=args
    )


def _greeting(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched greeting pattern", reply=GREETING_REPLY)


def _thanks(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched acknowledgement pattern", reply=THANKS_REPLY)


def _identity(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched capability question", reply=IDENTITY_REPLY)


def _datetime(query: str) -> RouteDecision:
    return _decision(Route.DATETIME, "matched date/time pattern")


def _calculator(query: str) -> RouteDecision:
    return _decision(Route.CALCULATOR, "query is a self-contained arithmetic expression")


def _translation(query: str) -> RouteDecision:
    parsed = parse_translation(query)
    text, language = parsed if parsed else ("", "")
    return _decision(
        Route.TRANSLATION, "matched translation request", text=text, language=language
    )


# Order matters: the first match wins, and the cheapest, most certain rules
# come first.
RULES: tuple[Rule, ...] = (
    Rule("greeting", lambda q: bool(_GREETING_RE.match(q)), _greeting),
    Rule("acknowledgement", lambda q: bool(_THANKS_RE.match(q)), _thanks),
    Rule("identity", lambda q: bool(_IDENTITY_RE.match(q)), _identity),
    Rule("datetime", lambda q: bool(_DATETIME_RE.match(q)), _datetime),
    Rule("arithmetic", looks_arithmetic, _calculator),
    Rule("translation", lambda q: parse_translation(q) is not None, _translation),
)


def apply_rules(query: str) -> RouteDecision | None:
    """First matching rule's decision, or None to fall through to the LLM.

    Deliberately returns None rather than guessing. Rules cover the queries
    that are unambiguous by shape; everything else is the classifier's job,
    and a rule that tried to cover ambiguous cases would be wrong silently.
    """
    text = (query or "").strip()
    if not text:
        return None
    for rule in RULES:
        try:
            if rule.matches(text):
                return rule.decide(text)
        except Exception:  # pragma: no cover - a rule must never break routing
            continue
    return None


def mentions_documents(query: str) -> bool:
    return bool(_DOCUMENT_RE.search(query or ""))


def suggests_web_search(query: str) -> bool:
    """Recency signal, suppressed when the query is clearly about documents.

    "What does our latest handbook say" contains 'latest' but must not go to
    the web — the document signal wins because being wrong in that direction
    answers an internal question from a public source.
    """
    text = query or ""
    return bool(_WEB_RE.search(text)) and not mentions_documents(text)
