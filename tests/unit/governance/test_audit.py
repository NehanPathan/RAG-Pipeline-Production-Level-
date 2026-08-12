from __future__ import annotations

import json
import uuid
from datetime import datetime

from src.governance.audit import AuditAction, AuditOutcome, _serialise


class TestAuditSerialisation:
    def test_none_stays_none(self):
        assert _serialise(None) is None

    def test_scalar_is_wrapped_not_dropped(self):
        """A settings change from 20 to 50 must record both sides, and both
        are scalars -- dropping them would make the entry useless."""
        assert _serialise(20) == {"value": 20}
        assert _serialise("bge") == {"value": "bge"}

    def test_dict_is_preserved(self):
        assert _serialise({"provider": "openai", "top_k": 20}) == {
            "provider": "openai",
            "top_k": 20,
        }

    def test_non_json_types_are_stringified(self):
        doc_id = uuid.uuid4()
        result = _serialise({"document_id": doc_id, "at": datetime(2026, 1, 1)})
        assert result["document_id"] == str(doc_id)
        assert isinstance(result["at"], str)

    def test_nested_structures_are_coerced(self):
        result = _serialise({"tags": ["a", "b"], "nested": {"id": uuid.uuid4()}})
        assert result["tags"] == ["a", "b"]
        assert isinstance(result["nested"]["id"], str)

    def test_output_is_always_json_serialisable(self):
        """The audit columns are JSONB; anything that cannot round-trip would
        fail the insert and silently lose the entry."""
        payload = _serialise(
            {"uuid": uuid.uuid4(), "when": datetime.now(), "n": 3, "ok": True, "list": [1, "x"]}
        )
        json.dumps(payload)


class TestAuditVocabulary:
    def test_actions_are_a_closed_set(self):
        """Free-text actions would make the trail unqueryable and blow up the
        Prometheus label cardinality."""
        values = [a.value for a in AuditAction]
        assert len(values) == len(set(values))
        assert "document_deleted" in values
        assert "kill_switch_toggled" in values
        assert "policy_violation" in values

    def test_outcomes_cover_denial(self):
        assert AuditOutcome.DENIED.value == "denied"
        assert {o.value for o in AuditOutcome} == {
            "allowed",
            "denied",
            "completed",
            "failed",
        }
