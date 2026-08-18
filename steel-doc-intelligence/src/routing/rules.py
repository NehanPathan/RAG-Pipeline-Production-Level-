from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.routing.routes import Route, RouteDecision, RouteSource
from src.tools.builtin.calculator import looks_arithmetic
from src.tools.builtin.translation import parse as parse_translation

# Whole-utterance greetings only: `(hi)` would match "hi" inside
# "which hire policy applies".
_GREETING_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|hiya|howdy|good\s+(morning|afternoon|evening)|"
    r"how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|sup)"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)

# A greeting opening a longer utterance. Used only by `smalltalk_reply`: as
# a routing rule it would hijack "hi, what section is CE-4".
_LEADING_GREETING_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|hiya|howdy|good\s+(morning|afternoon|evening))\b",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^\s*(?:ok(?:ay)?\s+)?"
    r"(?:thanks|thank\s+(?:you|u)|thx|tysm|ty|cheers|appreciate\s+it|"
    r"got\s+it|ok(?:ay)?|cool|nice|great|perfect|awesome|"
    r"bye|goodbye|see\s+(?:you|u)|good\s?night)"
    # Trailing intensifiers: "thanks a lot", "thank you so much", "thanks mate".
    r"(?:\s+(?:a\s+lot|so\s+much|very\s+much|much|mate|man|buddy|friend))?"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)

# "What can you do", in the spellings people actually type -- polite
# openers, `u` for `you`, both word orders.
#
# The `$` anchor is load-bearing: without it "what can you tell me about
# the weld at CE-4" matches here and gets a capability blurb, not a search.
_POLITE_OPENER = (
    r"(?:\s*(?:"
    r"please|pls|plz|"
    r"(?:may|can|could|would|will)\s+(?:i|you|u)|"
    r"i\s+(?:just\s+)?(?:want|need|would\s+like|'?d\s+like|wanna)(?:\s+to)?|"
    r"know|tell\s+me|let\s+me\s+know|ask|about|that|so|hey|hi|hello"
    r")\s*,?)*\s*"
)

#: `you` as people actually type it.
_YOU = r"(?:you|u|ya)"

_IDENTITY_RE = re.compile(
    r"^\s*" + _POLITE_OPENER + r"(?:"
    # who/what are you
    rf"wh(?:o|at)(?:'?s)?\s+(?:are|is|r)?\s*{_YOU}(?:\s+(?:for|about))?|"
    # what is this / what is this app
    r"wh(?:o|at)(?:'?s| is| are)?\s+(?:this|it)"
    r"(?:\s+(?:app|tool|system|assistant|thing|bot|site|place))?"
    r"(?:\s+(?:for|about|do(?:es)?))?|"
    # what can you do  AND  what you can do -- both word orders, because
    # "what u can do" is at least as common as "what can u do".
    rf"what\s+(?:all\s+)?(?:(?:can|do|does|could)\s+{_YOU}|{_YOU}\s+(?:can|do|could))"
    r"(?:\s+(?:do|help(?:\s+me)?(?:\s+with)?|offer|answer|support|handle|"
    r"tell\s+me|give\s+me|provide))?|"
    # how do you work
    rf"how\s+(?:do(?:es)?|can)\s+(?:{_YOU}|this|it)\s+(?:work|help)|"
    # asked from the other side: what should I ask
    r"what\s+(?:can|should|could|do)\s+i\s+ask(?:\s+" + _YOU + r")?|"
    r"what\s+(?:kind|sort|type)s?\s+of\s+questions?"
    r"(?:\s+(?:can|should|could|do)\s+i\s+ask(?:\s+" + _YOU + r")?)?|"
    r"(?:your|ur)\s+(?:capabilit(?:y|ies)|features?|purpose|job)|"
    r"help(?:\s+me)?"
    r")[\s!.,?]*$",
    re.IGNORECASE,
)

# The four shapes people use: "what is the date", "what time is it",
# "today's date", bare "time". The `$` keeps it off "what is the
# effective date of the contract".
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
# web rule, because "what does our latest policy say" contains "latest".
#
# The drawing vocabulary is here because the classifier got these wrong in
# the expensive direction: "what does the bent plate annotation refer to?"
# returned `llm_knowledge` and produced a textbook paragraph on sheet-metal
# bending while the drawing's own answer sat indexed and unread.
_DOCUMENT_RE = re.compile(
    r"\b(policy|policies|document|documents|handbook|contract|agreement|clause|"
    r"section|procedure|guideline|manual|report|spec|specification|sop|"
    r"according\s+to|in\s+the\s+(doc|file|pdf)|our\s+|company|internal"
    r"|drawing|sheet|detail|annotation|callout|leader|dimension|dimensions"
    r"|title\s*block|revision|layer|schedule|elevation|plan|section\s+view"
    r"|beams?|columns?|purlins?|girders?|joists?|plates?|bolts?|welds?|gussets?"
    r"|stiffeners?|baseplates?|base\s+plates?|anchors?|connections?|members?|steel"
    # Schedule vocabulary. Plurals spelled out, not assumed: `member`
    # does not match "members".
    r"|materials?|grades?|marks?|quantit(y|ies)|qty|weights?|thickness"
    # Visual vocabulary. A symbol or hatch question is about a mark on a
    # sheet, and the one kind that may escalate to vision -- which only the
    # retrieval path can do.
    r"|symbols?|hatch(ing)?|markings?|legend|unlabell?ed|unmarked"
    r"|callouts?|annotations?"
    r")\b",
    re.IGNORECASE,
)

# Material standards. A steel grade, not a topic worth preferring general
# knowledge over the sheet that specifies it.
_STANDARD_RE = re.compile(
    r"\b(ASTM|AISC|ASME|AWS|BS\s?EN|DIN|JIS|IS\s?\d{3,4}|A\d{2,4}|Fe\s?\d{3}|S\d{3})\b"
)

# A piece mark. The strongest document signal there is, and one the
# vocabulary list cannot cover because the surrounding words are ordinary.
# Asked "what material is BP-1?" the classifier once answered, from its
# own knowledge, that BP-1 is a biodegradable plastic. It is a base plate.
_PIECE_MARK_RE = re.compile(r"\b[A-Z]{1,4}-\d{1,4}\b")

GREETING_REPLY = (
    "Hello. I answer questions from the engineering documents indexed in this "
    "workspace — drawings, schedules, specifications and reports. "
    "What would you like to know?"
)

THANKS_REPLY = "You're welcome. Ask me anything else about the indexed documents."

IDENTITY_REPLY = (
    "I answer questions about the engineering documents indexed in this "
    "workspace — steel drawings, member and connection schedules, "
    "specifications, transmittals and reports, including native CAD files.\n\n"
    "Things I can do:\n"
    "- Look up a piece mark: *what section is CE-4, and which sheet is it on?*\n"
    "- Read a drawn schedule row: *show me the schedule row for BP-1*\n"
    "- Find a specification or note: *what weld size is called for at the base "
    "plate?*\n"
    "- Compare revisions: *what changed between Rev B and Rev C of S-104?*\n"
    "- Locate a drawing: *which sheet shows the base plate detail?*\n\n"
    "Every answer is drawn from the indexed documents and cites the sheet it "
    "came from. If the documents do not say, I will tell you that rather than "
    "guess — so a question I cannot answer usually means the source is not "
    "indexed, not that the answer is nothing."
)

#: For conversational input that is neither a greeting, an acknowledgement nor
#: a question about this system. Answering "Hello." to those was worse than
#: useless: it looked like the assistant had understood and had nothing to say.
#:
#: Says what the corpus covers and gives one concrete example, because "ask
#: something on topic" without naming the topic just moves the guesswork onto
#: the person asking.
OFF_TOPIC_REPLY = (
    "I only answer from the engineering documents indexed in this workspace — "
    "drawings, schedules, specifications and reports — so I cannot help with "
    "that one.\n\n"
    "Ask me something about the documents instead, for example *what section "
    "is CE-4?* or *which sheet shows the base plate detail?* Send **what can "
    "you do** for the full list."
)

#: Questions about the workspace rather than the documents -- who is signed
#: in, who holds what role. Real questions, none answerable from the corpus,
#: and a full pipeline run to refuse if they reach retrieval.
#:
#: Vetoed by document vocabulary (`_platform_question`): "who approved
#: drawing S-201" uses the word *user* and must still be retrieved.
_PLATFORM_RE = re.compile(
    r"\b("
    r"(?:who|which|what)\s+(?:is|are|'?s)?\s*(?:the\s+)?"
    r"(?:user|users|people|person|accounts?|members?)\s+"
    r"(?:logged?\s*(?:in|on)|signed?\s*(?:in|on)|online|active)|"
    # "who is logged in" — no noun at all between the wh-word and the verb.
    r"(?:who|which|what)\s+(?:is|are|'?s)?\s*(?:currently\s+)?"
    r"(?:logged?|signed?)\s*(?:in|on)|"
    r"logged?\s*in\s+users?|signed?\s*in\s+users?|"
    r"(?:list|show|get|see|display)\s+(?:me\s+)?(?:the\s+|all\s+|every\s+)*users?|"
    r"user\s+(?:information|info|list|details|accounts?)|"
    r"how\s+many\s+users?|"
    r"my\s+(?:account|role|clearance|permissions?|password|profile)|"
    r"(?:change|reset|update)\s+(?:my\s+)?password|"
    r"who\s+(?:has|have)\s+access\s+to\s+(?:this\s+)?(?:app|system|workspace|platform)|"
    r"(?:what|which)\s+(?:is\s+)?my\s+(?:role|clearance|permission)"
    r")\b",
    re.IGNORECASE,
)

#: Where a platform question is actually answered. Names the screen instead of
#: only declining, because "not in the documents" is true and unhelpful on its
#: own — the person asking wants the roster, not a boundary.
PLATFORM_REPLY = (
    "That is about this workspace rather than about the documents, so it is "
    "not something I can retrieve — the corpus holds drawings and "
    "specifications, not the user directory.\n\n"
    "You will find it under **Operations → Access**, which lists every "
    "account with its role, the clearance that role grants and the projects "
    "it belongs to. Roles are read from the database on every request, so "
    "that screen is the live answer rather than a cached one.\n\n"
    "If you meant something on a drawing instead — an approver in a title "
    "block, say — ask it naming the sheet and I will retrieve it."
)


@dataclass(frozen=True)
class Rule:
    name: str
    matches: Callable[[str], bool]
    decide: Callable[[str], RouteDecision]


def _decision(route: Route, reason: str, **args: Any) -> RouteDecision:
    return RouteDecision(
        route=route, source=RouteSource.RULE, confidence=1.0, reason=reason, args=args
    )


def _greeting(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched greeting pattern", reply=GREETING_REPLY)


def _thanks(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched acknowledgement pattern", reply=THANKS_REPLY)


def _identity(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched capability question", reply=IDENTITY_REPLY)


def _platform_question(query: str) -> bool:
    """Is this about the workspace rather than about a drawing?

    The veto is the point. `_PLATFORM_RE` matches the word *user*, and a
    drawing set is full of legitimate questions that use it — "which user
    approved S-201", "who signed off the inspection on sheet 4". Those name a
    document, so `mentions_documents` catches them and this rule declines,
    leaving them to retrieval where they belong.

    Wrong in the safe direction by construction: a false negative here costs
    one retrieval that ends in the same refusal as before, while a false
    positive would answer a real drawing question with a pointer to a settings
    screen.
    """
    return bool(_PLATFORM_RE.search(query)) and not mentions_documents(query)


def _platform(query: str) -> RouteDecision:
    return _decision(Route.GREETING, "matched platform question", reply=PLATFORM_REPLY)


def _datetime(query: str) -> RouteDecision:
    return _decision(Route.DATETIME, "matched date/time pattern")


def _calculator(query: str) -> RouteDecision:
    return _decision(Route.CALCULATOR, "query is a self-contained arithmetic expression")


def _translation(query: str) -> RouteDecision:
    parsed = parse_translation(query)
    text, language = parsed if parsed else ("", "")
    return _decision(Route.TRANSLATION, "matched translation request", text=text, language=language)


# Order matters: the first match wins, and the cheapest, most certain rules
# come first.
RULES: tuple[Rule, ...] = (
    Rule("greeting", lambda q: bool(_GREETING_RE.match(q)), _greeting),
    Rule("acknowledgement", lambda q: bool(_THANKS_RE.match(q)), _thanks),
    Rule("identity", lambda q: bool(_IDENTITY_RE.match(q)), _identity),
    # After identity so "what can you do" keeps its own answer, and before
    # datetime so nothing cheaper can claim it first.
    Rule("platform", _platform_question, _platform),
    Rule("datetime", lambda q: bool(_DATETIME_RE.match(q)), _datetime),
    Rule("arithmetic", looks_arithmetic, _calculator),
    Rule("translation", lambda q: parse_translation(q) is not None, _translation),
)


def smalltalk_reply(query: str) -> str:
    """Wording for a conversational turn, chosen from the query itself.

    The rules above attach a reply to the decisions they make. The *classifier*
    does not: it returns `greeting` for anything conversational — its prompt
    lists "a greeting, thanks, or a question about your own capabilities" under
    that one label — and carries no wording with it. The graph therefore fell
    back to a bare `"Hello."` for every classifier-routed turn, which is how
    *"may i know what u can do"* came to be answered with a greeting.

    So the reply is re-derived here instead of defaulted. The patterns are the
    same ones the rules use, applied without the anchoring assumption that a
    rule already had its chance — by the time this runs, one has.

    The fallback is the off-topic redirect rather than a greeting. If the
    classifier called something conversational and none of these patterns
    recognise it, "here is what I cover" is honest and useful; "Hello." is
    neither.
    """
    text = (query or "").strip()
    if not text:
        return GREETING_REPLY
    if _IDENTITY_RE.match(text):
        return IDENTITY_REPLY
    if _THANKS_RE.match(text):
        return THANKS_REPLY
    if _GREETING_RE.match(text):
        return GREETING_REPLY
    # A greeting with something attached — "hi, are you there" — is still a
    # greeting, and answering it with "I cannot help with that" would be rude
    # and wrong.
    if _GREETING_RE.match(text.split(",")[0]) or _LEADING_GREETING_RE.match(text):
        return GREETING_REPLY
    if _looks_misspelt_greeting(text):
        return GREETING_REPLY
    return OFF_TOPIC_REPLY


#: Greeting stems for the fuzzy check below.
_GREETING_WORDS = (
    "hi",
    "hey",
    "hello",
    "yo",
    "hiya",
    "howdy",
    "sup",
    "hai",
    "helo",
    "hola",
    "namaste",
    "salaam",
    "morning",
    "greetings",
)


def _looks_misspelt_greeting(text: str) -> bool:
    """Is this a one-word greeting someone typed loosely?

    "helooww" is a greeting. Matched literally it is nothing, so it fell to the
    off-topic reply — telling someone who said hello that their question was
    out of scope, which is a worse failure than the bare "Hello." this replaced.

    Runs of repeated letters are collapsed first ("helooww" -> "helow"), which
    is the whole of the enthusiasm case, and the remainder is compared for
    similarity. Deterministic and free: nothing here is worth a model call.

    Restricted to a single short word. A multi-word sentence that happens to
    sit near a greeting is far more likely to be a real question, and this
    must never swallow one.
    """
    token = re.sub(r"[^a-z]", "", text.lower())
    if not token or len(token) > 12 or len(text.split()) > 1:
        return False

    def squeeze(word: str) -> str:
        return re.sub(r"(.)\1+", r"\1", word)

    squeezed = squeeze(token)
    return any(
        squeezed == squeeze(word)
        or difflib.SequenceMatcher(None, squeezed, word).ratio() >= 0.8
        for word in _GREETING_WORDS
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
    """Whether this reads as a question about the indexed corpus.

    Matched on vocabulary or on a piece mark. Deliberately generous: routing a
    general question to retrieval costs one wasted search and still answers
    correctly, because an empty retrieval refuses. Routing a document question
    to model knowledge produces a fluent, uncited answer about the wrong
    thing.
    """
    text = query or ""
    return (
        bool(_DOCUMENT_RE.search(text))
        or bool(_PIECE_MARK_RE.search(text))
        or bool(_STANDARD_RE.search(text))
    )


def suggests_web_search(query: str) -> bool:
    """Recency signal, suppressed when the query is clearly about documents.

    "What does our latest handbook say" contains 'latest' but must not go to
    the web — the document signal wins because being wrong in that direction
    answers an internal question from a public source.
    """
    text = query or ""
    return bool(_WEB_RE.search(text)) and not mentions_documents(text)
