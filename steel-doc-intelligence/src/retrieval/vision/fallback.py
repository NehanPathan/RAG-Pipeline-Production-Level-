"""The vision fallback itself: governed, budgeted, cached, and subordinate.

Everything here runs only after `escalation.decide` has said a question is
genuinely visual and the deterministic pipeline has reported a gap. What is
left is to do it safely:

* **Governance first.** A crop of a drawing is document content leaving the
  deployment. It goes through the same provider allow-list and the same
  clearance rules as anything else, and a document classified above the
  configured ceiling is not sent -- the fallback records itself unavailable
  and the answer proceeds deterministically.
* **Budgeted.** Twenty retrieved chunks never mean twenty calls.
* **Cached** on document revision, region, model and prompt version, so a
  re-asked question costs nothing and a re-issued drawing costs a fresh look.
* **Subordinate.** The observation enters the context as evidence ranked
  below the file, and where it contradicts an extracted value the conflict is
  reported rather than resolved in vision's favour.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Protocol

from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.policy import AIPolicy
from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider, VisionUnsupportedError
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import (
    vision_cache_events,
    vision_fallback_latency,
    vision_fallback_total,
)
from src.retrieval.vision.models import (
    PROMPT_VERSION,
    EscalationDecision,
    EscalationReason,
    RegionCandidate,
    VisionBudget,
    VisionObservation,
    VisionOutcome,
)
from src.retrieval.vision.renderer import RegionRenderer, renderer_for

logger = get_logger(__name__)

VISION_PROMPT = """You are inspecting one small crop of an engineering drawing.

Describe only what is visible in this crop. Do not infer values that are not
drawn, do not estimate dimensions, and do not guess at part numbers.

The surrounding drawing has already been read by an exact CAD parser, which
reported that it could not identify the following, and that is the only thing
you are being asked about:

{context}

Question: {question}

Return ONLY valid JSON:
{{"observation": "<what is visible, one or two sentences>",
  "objects": ["<distinct things visible>"],
  "relationships": ["<how they relate, if visible>"],
  "uncertainties": ["<what you cannot determine from this crop>"],
  "confidence": <0.0 to 1.0, your genuine certainty>}}

If the crop does not show enough to answer, say so in "observation", list why
in "uncertainties" and give a low confidence. An honest low confidence is far
more useful than a plausible guess."""


class DocumentSource(Protocol):
    """Resolves a chunk's document to a readable file and its classification."""

    async def locate(self, document_id: str) -> tuple[Path | None, str, Sensitivity, str]: ...


class NullDocumentSource:
    """Used where no source is wired. Blocks the fallback rather than guessing."""

    async def locate(self, document_id: str) -> tuple[Path | None, str, Sensitivity, str]:
        return (None, "", Sensitivity.RESTRICTED, "")


class VisionCache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...


def cache_key(document_id: str, revision: str, region_signature: str, model_id: str) -> str:
    """Identity of one observation.

    Revision is in the key because a re-issued drawing is a different
    drawing -- Rev C's crop must never answer for Rev B. The prompt version is
    in it because a changed prompt asks a subtly different question, and
    serving the old answer for the new question is the kind of staleness
    nobody notices.
    """
    material = "|".join([document_id, revision or "-", region_signature, model_id, PROMPT_VERSION])
    return "vision:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


#: Values a drawing states exactly. Used only to notice that an observation
#: has contradicted one -- never to correct the observation, which would hide
#: the disagreement instead of reporting it.
#:
#: The alternation is ordered longest-first, and that ordering is the whole
#: correctness of it: tried the other way, `3/4"` matches as `4"`, so a model
#: that read the bolt correctly would be reported as contradicting the file.
_MEASUREMENT = re.compile(
    r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(?:\"|mm\b|in\b|ga\.?)", re.IGNORECASE
)


def find_conflicts(observation: VisionObservation, deterministic_text: str) -> list[str]:
    """Measurements the observation states that the extraction contradicts.

    The rule this enforces is the one that matters most: if the file says
    `3/4" A325` and the model reads `1/2"`, the file is right. It is the thing
    being described; the image is a rendering of it. Reporting the
    disagreement rather than silently dropping it is what lets a reader see
    that the crop was ambiguous.
    """
    seen = {m.group(0).strip().lower() for m in _MEASUREMENT.finditer(observation.observation)}
    stated = {m.group(0).strip().lower() for m in _MEASUREMENT.finditer(deterministic_text)}
    if not seen or not stated:
        return []
    contradicting = sorted(seen - stated)
    if not contradicting:
        return []
    return [
        f"The visual observation mentions {', '.join(contradicting)}, which the "
        f"extracted drawing text does not state. The extracted values "
        f"({', '.join(sorted(stated))}) are authoritative."
    ]


class VisionFallback:
    """Runs one escalation, or explains why it did not."""

    def __init__(
        self,
        provider: LLMProvider,
        policy: AIPolicy,
        source: DocumentSource | None = None,
        cache: VisionCache | None = None,
        *,
        max_sensitivity: Sensitivity = Sensitivity.INTERNAL,
        max_pixels: int = 1024,
        timeout_seconds: float = 20.0,
        min_confidence: float = 0.45,
        cache_ttl_seconds: int = 86_400,
        renderer: RegionRenderer | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._source = source or NullDocumentSource()
        self._cache = cache
        self._max_sensitivity = max_sensitivity
        self._max_pixels = max_pixels
        self._timeout = timeout_seconds
        self._min_confidence = min_confidence
        self._cache_ttl = cache_ttl_seconds
        self._renderer = renderer

    async def run(
        self,
        query: str,
        decision: EscalationDecision,
        budget: VisionBudget,
        deterministic_text: str = "",
    ) -> VisionOutcome:
        if not decision.escalate:
            vision_fallback_total.labels(outcome="not_escalated", reason=decision.reason).inc()
            return VisionOutcome(decision=decision)

        if not self._provider.supports_vision:
            # Not an error and not a silent provider switch: the fallback is
            # simply unavailable, and the deterministic answer stands.
            logger.info("vision_unavailable", model=self._provider.model_id)
            vision_fallback_total.labels(
                outcome="unavailable", reason=EscalationReason.PROVIDER_UNAVAILABLE
            ).inc()
            return VisionOutcome(
                decision=EscalationDecision.no(
                    EscalationReason.PROVIDER_UNAVAILABLE,
                    f"{self._provider.model_id} does not accept image input",
                )
            )

        allowed = self._policy.check_llm_provider(_provider_family(self._provider.model_id))
        if allowed.denied:
            logger.warning("vision_provider_not_allowed", model=self._provider.model_id)
            vision_fallback_total.labels(
                outcome="blocked", reason=EscalationReason.BLOCKED_BY_GOVERNANCE
            ).inc()
            return VisionOutcome(
                decision=EscalationDecision.no(
                    EscalationReason.BLOCKED_BY_GOVERNANCE, allowed.reason
                )
            )

        observations: list[VisionObservation] = []
        conflicts: list[str] = []
        error = ""

        for candidate in decision.candidates:
            if budget.exhausted:
                logger.info("vision_budget_exhausted", spent=budget.spent)
                vision_fallback_total.labels(
                    outcome="budget", reason=EscalationReason.BUDGET_EXHAUSTED
                ).inc()
                break

            observation, why = await self._inspect(query, candidate)
            if observation is None:
                error = error or why
                continue
            budget.charge()
            if observation.confidence < self._min_confidence:
                logger.info(
                    "vision_observation_discarded",
                    confidence=observation.confidence,
                    threshold=self._min_confidence,
                )
                vision_fallback_total.labels(outcome="low_confidence", reason=decision.reason).inc()
                continue
            observations.append(observation)
            conflicts.extend(find_conflicts(observation, deterministic_text))

        vision_fallback_total.labels(
            outcome="used" if observations else "no_observation", reason=decision.reason
        ).inc()
        return VisionOutcome(
            decision=decision, observations=observations, conflicts=conflicts, error=error
        )

    async def _inspect(
        self, query: str, candidate: RegionCandidate
    ) -> tuple[VisionObservation | None, str]:
        path, file_type, sensitivity, revision = await self._source.locate(candidate.document_id)
        if path is None or not path.exists():
            return None, "the source drawing is not reachable for rendering"

        # Classification gate. A crop of a restricted drawing is restricted
        # content, and sending it to an external model is exactly the export
        # the classification exists to prevent.
        if sensitivity.level > self._max_sensitivity.level:
            logger.warning(
                "vision_blocked_by_sensitivity",
                document_id=candidate.document_id,
                sensitivity=sensitivity.value,
                ceiling=self._max_sensitivity.value,
            )
            vision_fallback_total.labels(
                outcome="blocked", reason=EscalationReason.BLOCKED_BY_GOVERNANCE
            ).inc()
            return None, (
                f"the drawing is classified {sensitivity.value}, above the "
                f"{self._max_sensitivity.value} ceiling for external vision calls"
            )

        signature = _region_signature(candidate)
        key = cache_key(candidate.document_id, revision, signature, self._provider.model_id)
        if self._cache is not None:
            cached = await self._cache.get(key)
            if cached:
                vision_cache_events.labels(outcome="hit").inc()
                return _observation_from(cached, candidate, self._provider.model_id, True), ""
            vision_cache_events.labels(outcome="miss").inc()

        renderer = self._renderer or renderer_for(file_type)
        if renderer is None:
            return None, f"no renderer for {file_type or 'unknown'} files"

        image = await asyncio.to_thread(
            renderer.render,
            path,
            candidate.page_number,
            candidate.region,
            self._max_pixels,
        )
        if not image:
            return None, "the region could not be rendered"

        prompt = VISION_PROMPT.format(
            context=candidate.context[:600] or "(no context recorded)", question=query
        )
        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._provider.describe_image(prompt, image), timeout=self._timeout
            )
        except TimeoutError:
            logger.warning("vision_timeout", seconds=self._timeout)
            vision_fallback_total.labels(outcome="timeout", reason="timeout").inc()
            return None, f"the vision call exceeded {self._timeout:g}s"
        except VisionUnsupportedError:
            return None, "the provider does not accept image input"
        except Exception as exc:
            logger.warning("vision_call_failed", error=str(exc))
            vision_fallback_total.labels(outcome="error", reason="provider_error").inc()
            return None, f"the vision call failed: {exc}"
        finally:
            vision_fallback_latency.observe(time.perf_counter() - started)

        parsed = _parse(raw)
        if parsed is None:
            return None, "the vision model did not return usable JSON"

        if self._cache is not None:
            await self._cache.set(key, parsed, self._cache_ttl)
        return _observation_from(parsed, candidate, self._provider.model_id, False), ""


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        data = parse_json_response(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not str(data.get("observation", "")).strip():
        return None
    return data


def _observation_from(
    data: dict[str, Any], candidate: RegionCandidate, model_id: str, cached: bool
) -> VisionObservation:
    def strings(key: str) -> tuple[str, ...]:
        value = data.get(key) or []
        if isinstance(value, str):
            return (value,)
        return tuple(str(item) for item in value if str(item).strip())

    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return VisionObservation(
        observation=str(data.get("observation", "")).strip(),
        objects=strings("objects"),
        relationships=strings("relationships"),
        uncertainties=strings("uncertainties"),
        confidence=max(0.0, min(1.0, confidence)),
        region=candidate.region,
        document_id=candidate.document_id,
        document_name=candidate.document_name,
        model_id=model_id,
        cached=cached,
    )


def _region_signature(candidate: RegionCandidate) -> str:
    region = candidate.region
    return (
        f"{candidate.page_number}:{region.x0:.4f},{region.y0:.4f},"
        f"{region.x1:.4f},{region.y1:.4f}:{region.space}"
    )


def _provider_family(model_id: str) -> str:
    """The allow-list name for a model id, so the policy check matches."""
    lowered = (model_id or "").lower()
    for family in ("openai", "anthropic", "ollama", "openrouter", "azure"):
        if family in lowered:
            return family
    if lowered.startswith(("gpt-", "o1", "o3", "o4", "text-")):
        return "openai"
    if lowered.startswith("claude"):
        return "anthropic"
    return lowered.split("-")[0] or "unknown"


__all__ = [
    "VISION_PROMPT",
    "DocumentSource",
    "NullDocumentSource",
    "VisionCache",
    "VisionFallback",
    "cache_key",
    "find_conflicts",
]
