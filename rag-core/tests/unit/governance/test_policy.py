from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.policy import EnforcementMode, get_policy, reset_policy


@pytest.fixture(autouse=True)
def _reset_policy():
    reset_policy()
    yield
    reset_policy()


class TestSensitivityOrdering:
    def test_levels_are_strictly_increasing(self):
        levels = [s.level for s in Sensitivity]
        assert levels == sorted(levels)
        assert len(set(levels)) == len(levels)

    @pytest.mark.parametrize(
        "content,clearance,expected",
        [
            (Sensitivity.PUBLIC, Sensitivity.PUBLIC, True),
            (Sensitivity.INTERNAL, Sensitivity.PUBLIC, False),
            (Sensitivity.INTERNAL, Sensitivity.INTERNAL, True),
            (Sensitivity.RESTRICTED, Sensitivity.CONFIDENTIAL, False),
            (Sensitivity.PUBLIC, Sensitivity.RESTRICTED, True),
        ],
    )
    def test_readable_with(self, content, clearance, expected):
        assert content.readable_with(clearance) is expected

    def test_at_or_below_is_inclusive(self):
        allowed = Sensitivity.values_at_or_below(Sensitivity.CONFIDENTIAL)
        assert allowed == ["public", "internal", "confidential"]
        assert "restricted" not in allowed

    def test_public_clearance_sees_only_public(self):
        assert Sensitivity.values_at_or_below(Sensitivity.PUBLIC) == ["public"]


class TestSensitivityParsing:
    def test_unknown_value_falls_back_without_raising(self):
        assert Sensitivity.parse("top-secret", Sensitivity.INTERNAL) is Sensitivity.INTERNAL

    def test_missing_value_falls_back(self):
        assert Sensitivity.parse(None, Sensitivity.INTERNAL) is Sensitivity.INTERNAL
        assert Sensitivity.parse("", Sensitivity.INTERNAL) is Sensitivity.INTERNAL

    def test_parsing_is_case_and_whitespace_insensitive(self):
        assert Sensitivity.parse("  CONFIDENTIAL ", Sensitivity.PUBLIC) is Sensitivity.CONFIDENTIAL

    def test_unlabelled_data_never_resolves_to_public(self):
        """The core fail-closed property: a chunk indexed before
        classification existed must not become world-readable."""
        for raw in (None, "", "garbage"):
            assert Sensitivity.parse(raw, Sensitivity.INTERNAL) is not Sensitivity.PUBLIC


class TestClearanceMapping:
    def test_known_roles_map_to_configured_clearances(self):
        policy = get_policy()
        assert policy.clearance_for_role("viewer") is Sensitivity.PUBLIC
        assert policy.clearance_for_role("analyst") is Sensitivity.INTERNAL
        assert policy.clearance_for_role("steward") is Sensitivity.CONFIDENTIAL
        assert policy.clearance_for_role("admin") is Sensitivity.RESTRICTED

    def test_unknown_role_gets_default_not_maximum(self):
        policy = get_policy()
        clearance = policy.clearance_for_role("wizard")
        assert clearance is policy.default_clearance
        assert clearance is not Sensitivity.RESTRICTED

    def test_missing_role_gets_default(self):
        policy = get_policy()
        assert policy.clearance_for_role(None) is policy.default_clearance

    def test_role_lookup_is_case_insensitive(self):
        assert get_policy().clearance_for_role("ADMIN") is Sensitivity.RESTRICTED


class TestGroundingChecks:
    def test_no_context_is_refused(self):
        decision = get_policy().check_grounding(context_chunks=0, valid_citations=0)
        assert decision.denied
        assert decision.control_id == "C-GOV-03"

    def test_context_but_no_citations_is_refused(self):
        decision = get_policy().check_grounding(context_chunks=5, valid_citations=0)
        assert decision.denied
        assert decision.control_id == "C-GOV-04"

    def test_grounded_answer_is_allowed(self):
        decision = get_policy().check_grounding(context_chunks=5, valid_citations=2)
        assert decision.allowed

    def test_the_two_failures_are_distinguishable(self):
        """They are different defects -- no grounds vs ignored grounds -- and
        the pipeline refuses at different points for each."""
        policy = get_policy()
        no_context = policy.check_grounding(context_chunks=0, valid_citations=0)
        uncited = policy.check_grounding(context_chunks=3, valid_citations=0)
        assert no_context.control_id != uncited.control_id


class TestQualityFloors:
    def test_gated_metric_below_floor_is_denied(self):
        assert get_policy().check_quality("faithfulness", 0.10).denied

    def test_gated_metric_at_floor_passes(self):
        policy = get_policy()
        assert policy.check_quality("faithfulness", policy.min_faithfulness).allowed

    def test_ungated_metric_always_passes(self):
        policy = get_policy()
        assert policy.quality_floor("answer_correctness") is None
        assert policy.check_quality("answer_correctness", 0.01).allowed

    def test_unknown_metric_does_not_raise(self):
        assert get_policy().check_quality("made_up_metric", 0.0).allowed


class TestApprovedComponents:
    def test_approved_provider_passes(self):
        assert get_policy().check_llm_provider("openai").allowed

    def test_unapproved_provider_is_denied_with_control_id(self):
        decision = get_policy().check_llm_provider("some-startup-llm")
        assert decision.denied
        assert decision.control_id == "C-GOV-01"

    def test_provider_check_is_case_insensitive(self):
        assert get_policy().check_llm_provider("OpenAI").allowed

    def test_unapproved_embedding_model_is_denied(self):
        assert get_policy().check_embedding_model("word2vec-2013").denied


class TestPolicyShape:
    def test_policy_is_immutable(self):
        """A policy that request handlers can mutate is not a policy."""
        policy = get_policy()
        with pytest.raises(FrozenInstanceError):
            policy.min_faithfulness = 0.0  # type: ignore[misc]

    def test_default_enforcement_is_enforce(self):
        assert get_policy().enforcement_mode is EnforcementMode.ENFORCE
        assert get_policy().enforcing is True

    def test_as_dict_is_json_safe(self):
        import json

        json.dumps(get_policy().as_dict())

    def test_singleton_returns_same_instance(self):
        assert get_policy() is get_policy()
