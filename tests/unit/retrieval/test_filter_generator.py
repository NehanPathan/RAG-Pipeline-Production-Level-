import json
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from src.domain.value_objects.query_intent import IntentType, QueryIntent
from src.retrieval.agents.filter_generator import FilterGenerator


@pytest.fixture
def generator(mock_llm_provider):
    return FilterGenerator(llm_provider=mock_llm_provider)


@pytest.fixture
def intent():
    return QueryIntent(type=IntentType.POLICY_LOOKUP, confidence=0.9, domain="Operations")


@pytest.mark.asyncio
async def test_generate_combines_domain_with_llm_constraints(generator, mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps({"tags": ["refund", "policy"], "file_type": "pdf", "recency_days": None})
    )
    user_id = uuid.uuid4()

    spec = await generator.generate(
        query="recent refund pdf policy", intent=intent, selected_sources=["Operations"], user_id=user_id
    )

    assert spec.user_id == user_id
    assert spec.domain == "Operations"
    assert spec.tags == ["refund", "policy"]
    assert spec.file_type == "pdf"
    assert spec.date_from is None


@pytest.mark.asyncio
async def test_generate_converts_recency_days_to_date_from(generator, mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps({"tags": [], "file_type": None, "recency_days": 30})
    )

    spec = await generator.generate(
        query="latest policy", intent=intent, selected_sources=[], user_id=None
    )

    assert spec.date_from == date.today() - timedelta(days=30)


@pytest.mark.asyncio
async def test_generate_falls_back_to_minimal_filter_on_llm_exception(generator, mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    user_id = uuid.uuid4()

    spec = await generator.generate(
        query="anything", intent=intent, selected_sources=["Operations"], user_id=user_id
    )

    assert spec.user_id == user_id
    assert spec.domain == "Operations"
    assert spec.tags is None
    assert spec.file_type is None


@pytest.mark.asyncio
async def test_generate_domain_is_none_when_no_sources_selected(generator, mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(return_value=json.dumps({"tags": [], "file_type": None}))

    spec = await generator.generate(query="anything", intent=intent, selected_sources=[], user_id=None)

    assert spec.domain is None
