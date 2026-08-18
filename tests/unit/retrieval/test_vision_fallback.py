"""The vision fallback, which mostly must not fire.

Almost every test here is a refusal, because almost every question about a
drawing is answered by reading it. The failure this guards against is not a
missing vision call -- it is a vision call that produces a confident second
opinion about a file that already said the answer.

Model calls are mocked throughout: what is under test is the decision, the
governance, the budget and the ranking, none of which need a real provider.
"""

from __future__ import annotations

import asyncio

import pytest

from src.domain.value_objects.provenance import Region
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.policy import AIPolicy, get_policy
from src.llm.providers.base import LLMProvider, VisionUnsupportedError
from src.retrieval.vision.escalation import decide, looks_visual
from src.retrieval.vision.fallback import VisionFallback, cache_key, find_conflicts
from src.retrieval.vision.models import (
    EscalationDecision,
    EscalationReason,
    RegionCandidate,
    VisionBudget,
    VisionObservation,
)

REGION = Region(page_number=1, x0=0.8, y0=0.2, x1=0.95, y1=0.3, space="sheet")

UNRESOLVED = (
    "doc-1",
    "SSD09.0-02.dxf",
    "Annotations on sheet 1 whose target could not be determined from the drawing.",
    1,
    [REGION],
    ["S-TEXT"],
)
RESOLVED = (
    "doc-1",
    "SSD09.0-02.dxf",
    'The annotation "BENT PLATE 5x5x 12 GA." labels a S-SECT_STEEL_THRU entity, '
    "connected to it by a leader line.",
    1,
    [REGION],
    ["S-TEXT"],
)


class _Provider(LLMProvider):
    """A vision-capable provider that returns whatever the test tells it to."""

    def __init__(self, reply: str = "", vision: bool = True, fail: Exception | None = None):
        self.reply = reply
        self.vision = vision
        self.fail = fail
        self.calls = 0

    async def complete(self, prompt, max_tokens=1024, temperature=0.3):
        return ""

    def stream(self, prompt, max_tokens=1024, temperature=0.3):
        raise NotImplementedError

    @property
    def model_id(self) -> str:
        return "openai/gpt-4o"

    @property
    def supports_vision(self) -> bool:
        return self.vision

    async def describe_image(self, prompt, image_png, max_tokens=512, temperature=0.0):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self.reply


class _Source:
    def __init__(self, sensitivity=Sensitivity.INTERNAL, exists=True, revision="A"):
        self.sensitivity = sensitivity
        self.exists = exists
        self.revision = revision

    async def locate(self, document_id):
        from pathlib import Path

        path = Path(__file__) if self.exists else Path("/nowhere/missing.dxf")
        return (path, "dxf", self.sensitivity, self.revision)


class _Renderer:
    def __init__(self, image: bytes | None = b"PNG"):
        self.image = image
        self.calls = 0

    def render(self, source, page_number, region, max_pixels):
        self.calls += 1
        return self.image


class _Cache:
    def __init__(self, seeded=None):
        self.store = dict(seeded or {})
        self.writes = 0

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl):
        self.store[key] = value
        self.writes += 1


GOOD_JSON = (
    '{"observation": "A filled triangular arrowhead on a leader line.",'
    ' "objects": ["arrowhead", "leader"], "relationships": ["arrowhead terminates leader"],'
    ' "uncertainties": ["what the leader points at is outside the crop"],'
    ' "confidence": 0.8}'
)


def fallback(provider=None, source=None, cache=None, renderer=None, **kwargs):
    return VisionFallback(
        provider=provider or _Provider(GOOD_JSON),
        policy=kwargs.pop("policy", None) or get_policy(),
        source=source or _Source(),
        cache=cache,
        renderer=renderer or _Renderer(),
        **kwargs,
    )


# When not to call vision. The bulk of the specification.


class TestVisionIsNotCalled:
    @pytest.mark.parametrize(
        "query",
        [
            "What is the bolt diameter?",
            "What is the plate thickness?",
            "What is the quantity of BP-1?",
            "What revision is this drawing?",
            "Which members use ASTM A36?",
        ],
    )
    def test_a_question_about_a_stated_value_is_not_visual(self, query):
        """CASE A and CASE B. The drawing states these in words; a picture
        cannot improve on the file that is being pictured."""
        assert looks_visual(query) is False
        assert decide(query, [UNRESOLVED]).escalate is False

    def test_no_retrieval_at_all_does_not_escalate(self):
        """CASE E. Nothing retrieved means either a bug or a drawing that does
        not carry the answer, and a picture cannot tell those apart -- it can
        only produce something."""
        decision = decide("What symbol is shown here?", [])
        assert decision.escalate is False
        assert decision.reason == EscalationReason.NO_EVIDENCE_AT_ALL

    def test_resolved_evidence_does_not_escalate(self):
        """The extraction followed the leader and named the target. There is
        no gap for a picture to fill."""
        decision = decide("What symbol does this annotation point to?", [RESOLVED])
        assert decision.escalate is False
        assert decision.reason == EscalationReason.DETERMINISTIC_EVIDENCE_SUFFICIENT

    def test_an_unresolved_passage_with_no_region_does_not_escalate(self):
        """Rendering the whole sheet instead would be exactly the
        send-the-drawing-to-a-model behaviour this design avoids."""
        passage = (*UNRESOLVED[:4], [], UNRESOLVED[5])
        decision = decide("What symbol is this?", [passage])
        assert decision.escalate is False
        assert decision.reason == EscalationReason.NO_REGION_TO_INSPECT

    def test_the_flag_switches_it_off_entirely(self):
        decision = decide("What symbol is this?", [UNRESOLVED], enabled=False)
        assert decision.escalate is False
        assert decision.reason == EscalationReason.DISABLED

    @pytest.mark.asyncio
    async def test_a_refusal_never_reaches_the_provider(self):
        provider = _Provider(GOOD_JSON)
        outcome = await fallback(provider).run(
            "What is the bolt diameter?",
            decide("What is the bolt diameter?", [UNRESOLVED]),
            VisionBudget(),
        )
        assert provider.calls == 0
        assert outcome.used is False


class TestVisionIsCalled:
    """CASE C and CASE D: a genuinely visual question over an unresolved region."""

    def test_an_unresolved_symbol_question_escalates(self):
        decision = decide("What does this unlabelled symbol represent?", [UNRESOLVED])
        assert decision.escalate is True
        assert decision.reason == EscalationReason.UNRESOLVED_SYMBOL
        assert len(decision.candidates) == 1
        assert decision.candidates[0].region == REGION

    def test_only_the_relevant_region_is_selected(self):
        """Never the whole drawing."""
        decision = decide("What symbol is shown inside this connection?", [UNRESOLVED])
        assert decision.candidates[0].region.x1 - decision.candidates[0].region.x0 < 0.5

    def test_the_region_count_is_capped(self):
        many = [
            (f"doc-{i}", "d.dxf", UNRESOLVED[2], 1, [REGION, REGION, REGION], []) for i in range(10)
        ]
        decision = decide("What symbol is this?", many, max_regions=2)
        assert len(decision.candidates) == 2

    @pytest.mark.asyncio
    async def test_a_structured_observation_comes_back(self):
        provider = _Provider(GOOD_JSON)
        decision = decide("What does this unlabelled symbol represent?", [UNRESOLVED])
        outcome = await fallback(provider).run("What symbol?", decision, VisionBudget())

        assert provider.calls == 1
        assert len(outcome.observations) == 1
        observation = outcome.observations[0]
        assert observation.objects == ("arrowhead", "leader")
        assert observation.uncertainties
        assert observation.confidence == pytest.approx(0.8)
        assert observation.region == REGION

    @pytest.mark.asyncio
    async def test_the_observation_is_labelled_as_weaker_than_the_file(self):
        """It enters the context as evidence, never as an answer, and the
        wording is what stops a generator quoting it as a fact."""
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback().run("What symbol?", decision, VisionBudget())
        evidence = outcome.observations[0].as_evidence()
        assert "VISUAL OBSERVATION" in evidence
        assert "not stated by the file" in evidence
        assert "the extracted value is correct" in evidence


class TestBudgetAndCost:
    @pytest.mark.asyncio
    async def test_twenty_candidates_do_not_mean_twenty_calls(self):
        provider = _Provider(GOOD_JSON)
        candidates = tuple(
            RegionCandidate("doc-1", "d.dxf", 1, REGION, "unresolved") for _ in range(20)
        )
        decision = EscalationDecision.yes(EscalationReason.UNRESOLVED_SYMBOL, candidates)
        await fallback(provider).run("What symbol?", decision, VisionBudget(max_calls=2))
        assert provider.calls == 2

    def test_the_budget_counts_down(self):
        budget = VisionBudget(max_calls=2)
        budget.charge()
        assert budget.remaining == 1 and not budget.exhausted
        budget.charge()
        assert budget.exhausted


class TestGovernance:
    @pytest.mark.asyncio
    async def test_a_document_above_the_ceiling_is_never_rendered(self):
        """A crop of a restricted drawing is restricted content, and sending
        it out is exactly the export the classification exists to prevent."""
        provider = _Provider(GOOD_JSON)
        renderer = _Renderer()
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(
            provider,
            source=_Source(sensitivity=Sensitivity.RESTRICTED),
            renderer=renderer,
            max_sensitivity=Sensitivity.INTERNAL,
        ).run("What symbol?", decision, VisionBudget())

        assert renderer.calls == 0, "the crop must not even be rendered"
        assert provider.calls == 0
        assert outcome.used is False
        assert "restricted" in outcome.error

    @pytest.mark.asyncio
    async def test_a_provider_outside_the_allow_list_is_blocked(self):
        policy = AIPolicy(
            **{
                **get_policy().__dict__,
                "allowed_llm_providers": frozenset({"ollama"}),
            }
        )
        provider = _Provider(GOOD_JSON)
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider, policy=policy).run(
            "What symbol?", decision, VisionBudget()
        )
        assert provider.calls == 0
        assert outcome.decision.reason == EscalationReason.BLOCKED_BY_GOVERNANCE

    @pytest.mark.asyncio
    async def test_a_text_only_provider_degrades_rather_than_crashes(self):
        """Not an error and not a silent provider switch. The fallback is
        unavailable and the deterministic answer stands."""
        provider = _Provider(GOOD_JSON, vision=False)
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider).run("What symbol?", decision, VisionBudget())
        assert provider.calls == 0
        assert outcome.decision.reason == EscalationReason.PROVIDER_UNAVAILABLE

    def test_the_base_provider_refuses_images_by_default(self):
        class Plain(LLMProvider):
            async def complete(self, prompt, max_tokens=1024, temperature=0.3):
                return ""

            def stream(self, prompt, max_tokens=1024, temperature=0.3):
                raise NotImplementedError

            @property
            def model_id(self):
                return "text-only"

        provider = Plain()
        assert provider.supports_vision is False
        with pytest.raises(VisionUnsupportedError):
            asyncio.run(provider.describe_image("p", b""))


class TestVisionNeverOverridesTheFile:
    def test_a_contradicted_measurement_is_reported_not_applied(self):
        """If the file says 3/4" and the model reads 1/2", the file is right:
        it is the thing being described, not a photograph of it."""
        observation = VisionObservation(
            observation='The bolt appears to be 1/2" diameter.', confidence=0.9
        )
        conflicts = find_conflicts(observation, 'ALL BOLTS 3/4" DIA. A325')
        assert conflicts
        assert "authoritative" in conflicts[0]
        assert '1/2"' in conflicts[0]

    def test_agreement_produces_no_conflict(self):
        observation = VisionObservation(observation='Marked 3/4" on the leader.')
        assert find_conflicts(observation, 'ALL BOLTS 3/4" DIA. A325') == []

    def test_an_observation_with_no_measurement_produces_no_conflict(self):
        observation = VisionObservation(observation="A triangular arrowhead.")
        assert find_conflicts(observation, 'ALL BOLTS 3/4" DIA.') == []


class TestConfidence:
    @pytest.mark.asyncio
    async def test_a_low_confidence_observation_is_discarded(self):
        """A model that is unsure has produced a guess, and a guess in the
        context is a guess in the answer."""
        provider = _Provider('{"observation": "possibly a weld symbol", "confidence": 0.2}')
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider, min_confidence=0.45).run(
            "What symbol?", decision, VisionBudget()
        )
        assert provider.calls == 1, "the call happened"
        assert outcome.observations == [], "and its answer was not used"

    @pytest.mark.asyncio
    async def test_confidence_is_clamped_not_trusted_blindly(self):
        provider = _Provider('{"observation": "x", "confidence": 7.5}')
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider).run("What symbol?", decision, VisionBudget())
        assert outcome.observations[0].confidence == 1.0


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_a_timeout_is_reported_not_raised(self):
        class Slow(_Provider):
            async def describe_image(self, prompt, image_png, max_tokens=512, temperature=0.0):
                self.calls += 1
                await asyncio.sleep(5)
                return ""

        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(Slow(GOOD_JSON), timeout_seconds=0.05).run(
            "What symbol?", decision, VisionBudget()
        )
        assert outcome.used is False
        assert "exceeded" in outcome.error

    @pytest.mark.asyncio
    async def test_unparseable_output_is_discarded(self):
        provider = _Provider("I think it's a weld symbol, probably.")
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider).run("What symbol?", decision, VisionBudget())
        assert outcome.used is False
        assert "JSON" in outcome.error

    @pytest.mark.asyncio
    async def test_a_provider_error_does_not_break_the_answer(self):
        provider = _Provider(GOOD_JSON, fail=RuntimeError("upstream 500"))
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider).run("What symbol?", decision, VisionBudget())
        assert outcome.used is False
        assert "failed" in outcome.error

    @pytest.mark.asyncio
    async def test_a_region_that_cannot_be_rendered_is_skipped(self):
        provider = _Provider(GOOD_JSON)
        decision = decide("What symbol is this?", [UNRESOLVED])
        outcome = await fallback(provider, renderer=_Renderer(image=None)).run(
            "What symbol?", decision, VisionBudget()
        )
        assert provider.calls == 0
        assert "rendered" in outcome.error


class TestCache:
    @pytest.mark.asyncio
    async def test_a_second_identical_question_is_free(self):
        provider = _Provider(GOOD_JSON)
        cache = _Cache()
        decision = decide("What symbol is this?", [UNRESOLVED])

        first = fallback(provider, cache=cache)
        await first.run("What symbol?", decision, VisionBudget())
        assert provider.calls == 1

        await first.run("What symbol?", decision, VisionBudget())
        assert provider.calls == 1, "the second call was served from cache"

    @pytest.mark.asyncio
    async def test_a_cached_observation_is_marked_as_cached(self):
        cache = _Cache()
        decision = decide("What symbol is this?", [UNRESOLVED])
        service = fallback(cache=cache)
        await service.run("What symbol?", decision, VisionBudget())
        outcome = await service.run("What symbol?", decision, VisionBudget())
        assert outcome.observations[0].cached is True

    def test_a_new_revision_invalidates_the_observation(self):
        """Rev C's crop must never answer for Rev B."""
        a = cache_key("doc-1", "B", "1:0.1,0.2,0.3,0.4:sheet", "gpt-4o")
        b = cache_key("doc-1", "C", "1:0.1,0.2,0.3,0.4:sheet", "gpt-4o")
        assert a != b

    def test_a_different_region_or_model_is_a_different_key(self):
        base = cache_key("doc-1", "B", "1:0.1,0.2,0.3,0.4:sheet", "gpt-4o")
        assert base != cache_key("doc-1", "B", "1:0.9,0.2,0.3,0.4:sheet", "gpt-4o")
        assert base != cache_key("doc-1", "B", "1:0.1,0.2,0.3,0.4:sheet", "claude-opus-5")

    def test_a_changed_prompt_version_invalidates_everything(self):
        """A changed prompt asks a subtly different question, and serving the
        old answer for it is the staleness nobody notices."""
        import src.retrieval.vision.models as models

        before = cache_key("doc-1", "B", "sig", "gpt-4o")
        original = models.PROMPT_VERSION
        try:
            models.PROMPT_VERSION = "v2"
            import importlib

            import src.retrieval.vision.fallback as fb

            importlib.reload(fb)
            after = fb.cache_key("doc-1", "B", "sig", "gpt-4o")
        finally:
            models.PROMPT_VERSION = original
            import importlib

            import src.retrieval.vision.fallback as fb

            importlib.reload(fb)
        assert before != after


class TestGatewayDelegation:
    """The gateway is an LLMProvider, so it must not answer the base class's
    "no" on behalf of a chain that can see."""

    def _gateway(self, *providers):
        from src.llm.gateway import LLMGateway, ProviderBinding

        return LLMGateway(
            chain=[ProviderBinding(name=f"p{i}", provider=p) for i, p in enumerate(providers)],
            role="large",
        )

    def test_a_vision_capable_chain_reports_vision(self):
        """Inherited rather than delegated, this returned False and switched
        the fallback off on a correctly configured gpt-4o deployment."""
        assert self._gateway(_Provider(GOOD_JSON, vision=True)).supports_vision is True

    def test_a_text_only_chain_reports_none(self):
        assert self._gateway(_Provider(GOOD_JSON, vision=False)).supports_vision is False

    def test_a_mixed_chain_reports_vision(self):
        gateway = self._gateway(
            _Provider(GOOD_JSON, vision=False), _Provider(GOOD_JSON, vision=True)
        )
        assert gateway.supports_vision is True

    @pytest.mark.asyncio
    async def test_a_blind_provider_is_skipped_not_failed(self):
        blind = _Provider(GOOD_JSON, vision=False)
        seeing = _Provider(GOOD_JSON, vision=True)
        result = await self._gateway(blind, seeing).describe_image("p", b"PNG")
        assert blind.calls == 0
        assert seeing.calls == 1
        assert "arrowhead" in result
