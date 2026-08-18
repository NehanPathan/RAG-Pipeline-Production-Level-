from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import pii_redactions
from src.plugins import PluginRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class PIIMatch:
    """One detection. `value` is never logged — logging the PII you just
    redacted would defeat the redaction."""

    detector: str
    start: int
    end: int
    value: str

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class RedactionResult:
    text: str
    matches: list[PIIMatch] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.matches)

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in self.matches:
            counts[match.detector] = counts.get(match.detector, 0) + 1
        return counts


@dataclass(frozen=True)
class Detector:
    """One PII pattern plus an optional validator.

    The validator is what separates a usable detector from an annoying one:
    a 16-digit regex matches order numbers as readily as card numbers, and
    a Luhn check removes almost all of those false positives.
    """

    name: str
    pattern: re.Pattern[str]
    placeholder: str
    validate: Callable[[str], bool] | None = None
    #: Keep the last N characters visible. A fully-masked value destroys the
    #: reader's ability to tell two redactions apart ("was that *my* card?"),
    #: which matters when a human is reviewing a redacted answer.
    keep_tail: int = 0

    def mask(self, value: str) -> str:
        if self.keep_tail and len(value) > self.keep_tail:
            return f"[{self.placeholder}:…{value[-self.keep_tail:]}]"
        return f"[{self.placeholder}]"


detectors: PluginRegistry[Detector] = PluginRegistry("pii_detector")


def _luhn(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _valid_ssn(value: str) -> bool:
    """Reject the structurally impossible SSNs that a bare regex accepts.

    Without this, dates like 123-45-6789 in a document are flagged constantly.
    """
    digits = re.sub(r"\D", "", value)
    if len(digits) != 9 or digits == "0" * 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return area not in {"000", "666"} and not area.startswith("9") and group != "00" and serial != "0000"


def _plausible_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    # Below 10 digits it is almost always a reference number, above 15 it
    # exceeds the E.164 maximum and is therefore not a phone number.
    return 10 <= len(digits) <= 15


BUILTIN_DETECTORS: tuple[Detector, ...] = (
    Detector(
        name="email",
        pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        placeholder="EMAIL",
    ),
    Detector(
        name="credit_card",
        pattern=re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        placeholder="CARD",
        validate=_luhn,
        keep_tail=4,
    ),
    Detector(
        name="ssn",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        placeholder="SSN",
        validate=_valid_ssn,
    ),
    Detector(
        name="phone",
        # Requires a separator or leading +, so bare digit runs (invoice numbers,
        # years) are not swept up. The trailing guard is `(?!\d)(?!\.\d)`, not
        # `(?![\w.])` -- the latter refused every phone number ending a sentence
        # ("call 555-123-4567.") while still needing to exclude `.100` octets.
        pattern=re.compile(
            r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])"
            r"\d{2,4}[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4})?(?!\d)(?!\.\d)"
        ),
        placeholder="PHONE",
        validate=_plausible_phone,
        keep_tail=2,
    ),
    Detector(
        name="iban",
        pattern=re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        placeholder="IBAN",
        keep_tail=4,
    ),
    Detector(
        name="ip_address",
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
        ),
        placeholder="IP",
    ),
    Detector(
        name="api_key",
        # Provider-prefixed secrets. Worth catching separately from generic
        # tokens: a leaked key in a retrieved document is an active incident,
        # not just a privacy issue.
        pattern=re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,}|"
            r"ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b"
        ),
        placeholder="SECRET",
    ),
    Detector(
        name="passport",
        pattern=re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
        placeholder="PASSPORT",
    ),
    Detector(
        name="date_of_birth",
        pattern=re.compile(
            r"\b(?:DOB|D\.O\.B\.?|date\s+of\s+birth)\s*[:\-]?\s*"
            r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b",
            re.IGNORECASE,
        ),
        placeholder="DOB",
    ),
)

for _detector in BUILTIN_DETECTORS:
    detectors.add(_detector.name, (lambda d=_detector: d), description=f"{_detector.name} detector")


class PIIRedactor:
    """Detects and masks personal data in text.

    Applied at two points, for different reasons:

      **Retrieved context, before generation.** Stops PII reaching the LLM
      provider at all. This is the point that matters for data egress: once a
      passage is in the prompt, it has left the deployment regardless of what
      the answer says.

      **Generated answers, before display.** Catches PII the model
      reconstructed or carried over from conversation history, which context
      redaction alone cannot reach.

    Over-redaction is a real cost, not a free safety margin: a redacted
    passage the model needed produces a worse answer. The detectors therefore
    validate (Luhn, SSN structure, phone length) rather than matching on
    shape alone, and `passport`/`ip_address` are the ones most worth
    disabling per-deployment via `GOVERNANCE_PII_DETECTORS` if the corpus
    contains many identifier-like strings.
    """

    def __init__(self, enabled_detectors: Iterable[str] | None = None) -> None:
        names = list(enabled_detectors or [])
        if names:
            unknown = [n for n in names if not detectors.has(n)]
            if unknown:
                logger.warning(
                    "pii_detector_unknown_ignored", unknown=unknown, known=detectors.names()
                )
            self._detectors = [detectors.create(n) for n in names if detectors.has(n)]
        else:
            self._detectors = [detectors.create(n) for n in detectors.names()]

    @property
    def detector_names(self) -> list[str]:
        return [d.name for d in self._detectors]

    def scan(self, text: str) -> list[PIIMatch]:
        """Find matches without modifying anything.

        Overlaps are resolved by keeping the longest match: an email contains
        a substring that the passport pattern can match, and masking the
        shorter one first would leave fragments of the longer one exposed.
        """
        if not text:
            return []

        found: list[PIIMatch] = []
        for detector in self._detectors:
            for match in detector.pattern.finditer(text):
                value = match.group(0)
                if detector.validate and not detector.validate(value):
                    continue
                found.append(
                    PIIMatch(detector=detector.name, start=match.start(), end=match.end(), value=value)
                )

        found.sort(key=lambda m: (m.start, -m.length))
        deduped: list[PIIMatch] = []
        last_end = -1
        for match in found:
            if match.start >= last_end:
                deduped.append(match)
                last_end = match.end
        return deduped

    def redact(self, text: str, *, surface: str = "unknown") -> RedactionResult:
        matches = self.scan(text)
        if not matches:
            return RedactionResult(text=text)

        by_name = {d.name: d for d in self._detectors}
        pieces: list[str] = []
        cursor = 0
        for match in matches:
            pieces.append(text[cursor : match.start])
            pieces.append(by_name[match.detector].mask(match.value))
            cursor = match.end
        pieces.append(text[cursor:])

        result = RedactionResult(text="".join(pieces), matches=matches)
        for detector_name, count in result.counts.items():
            pii_redactions.labels(detector=detector_name, surface=surface).inc(count)
        # Counts only — never the values. A log line containing the PII this
        # function just removed would move the leak rather than close it.
        logger.info("pii_redacted", surface=surface, **result.counts)
        return result

    def redact_all(self, texts: list[str], *, surface: str = "unknown") -> tuple[list[str], int]:
        """Redact a list of passages. Returns (texts, total matches)."""
        redacted: list[str] = []
        total = 0
        for text in texts:
            result = self.redact(text, surface=surface)
            redacted.append(result.text)
            total += len(result.matches)
        return redacted, total


_redactor: PIIRedactor | None = None
_question_redactor: PIIRedactor | None = None


def get_redactor() -> PIIRedactor:
    global _redactor
    if _redactor is None:
        from src.config import get_settings

        settings = get_settings()
        _redactor = PIIRedactor(settings.governance_pii_detectors_list)
        logger.info("pii_redactor_ready", detectors=_redactor.detector_names)
    return _redactor


def get_question_redactor() -> PIIRedactor:
    """A deliberately narrower redactor for the user's own question.

    The question is not just content, it is the **search key**: it gets
    embedded and matched against the corpus. Masking an email address someone
    is legitimately searching for would silently break their query, so the
    full detector set is the wrong tool here.

    The default set (`api_key`, `credit_card`, `ssn`, `iban`) is chosen on one
    test: values with no plausible search utility and high harm if they reach
    a third-party provider or the message log. A pasted API key is an active
    incident; an email address in a question usually is not.
    """
    global _question_redactor
    if _question_redactor is None:
        from src.config import get_settings

        settings = get_settings()
        _question_redactor = PIIRedactor(settings.governance_pii_question_detectors_list)
        logger.info("pii_question_redactor_ready", detectors=_question_redactor.detector_names)
    return _question_redactor


def reset_redactor() -> None:
    """Test helper: rebuild from settings on next access."""
    global _redactor, _question_redactor
    _redactor = None
    _question_redactor = None
