import json
from unittest.mock import AsyncMock

import pytest

from src.domain.value_objects.query_intent import IntentType
from src.retrieval.agents.intent_classifier import IntentClassifier


@pytest.fixture
def classifier(mock_llm_provider):
    return IntentClassifier(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_classify_returns_parsed_intent(classifier, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps({"intent": "policy_lookup", "confidence": 0.92, "domain": "Operations"})
    )

    intent = await classifier.classify("What is the refund policy?")

    assert intent.type == IntentType.POLICY_LOOKUP
    assert intent.confidence == 0.92
    assert intent.domain == "Operations"


@pytest.mark.asyncio
async def test_classify_falls_back_on_invalid_json(classifier, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="not json at all")

    intent = await classifier.classify("hello")

    assert intent.type == IntentType.FACTUAL
    assert intent.confidence == 0.0
    assert intent.domain == "General"


@pytest.mark.asyncio
async def test_classify_falls_back_intent_type_but_keeps_other_fields(classifier, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps({"intent": "totally_unknown", "confidence": 0.8, "domain": "Legal"})
    )

    intent = await classifier.classify("some query")

    assert intent.type == IntentType.FACTUAL
    assert intent.confidence == 0.8
    assert intent.domain == "Legal"


@pytest.mark.asyncio
async def test_classify_handles_llm_exception(classifier, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    intent = await classifier.classify("hello")

    assert intent.type == IntentType.FACTUAL
    assert intent.confidence == 0.0
