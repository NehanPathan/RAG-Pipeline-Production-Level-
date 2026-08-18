import json
from unittest.mock import AsyncMock

import pytest

from src.retrieval.agents.query_expander import QueryExpander


@pytest.fixture
def expander(mock_llm_provider):
    return QueryExpander(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_expand_returns_variants(expander, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps(["return process", "money back rules"])
    )

    variants = await expander.expand("refund policy", count=3)

    assert variants == ["return process", "money back rules"]


@pytest.mark.asyncio
async def test_expand_truncates_to_requested_count(expander, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(
        return_value=json.dumps(["a", "b", "c", "d", "e"])
    )

    variants = await expander.expand("query", count=2)

    assert variants == ["a", "b"]


@pytest.mark.asyncio
async def test_expand_returns_empty_list_when_response_is_not_a_list(expander, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value=json.dumps({"not": "a list"}))

    variants = await expander.expand("query")

    assert variants == []


@pytest.mark.asyncio
async def test_expand_returns_empty_list_on_llm_exception(expander, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    variants = await expander.expand("query")

    assert variants == []
