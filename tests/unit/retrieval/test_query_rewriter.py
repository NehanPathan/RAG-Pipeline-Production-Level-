from unittest.mock import AsyncMock

import pytest

from src.retrieval.agents.query_rewriter import QueryRewriter


@pytest.fixture
def rewriter(mock_llm_provider):
    return QueryRewriter(llm_provider=mock_llm_provider)


@pytest.mark.asyncio
async def test_rewrite_returns_cleaned_text(rewriter, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(
        return_value='"What is the product return and refund policy?"'
    )

    result = await rewriter.rewrite("refund policy??")

    assert result == "What is the product return and refund policy?"


@pytest.mark.asyncio
async def test_rewrite_falls_back_to_original_on_empty_response(rewriter, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(return_value="   ")

    result = await rewriter.rewrite("refund policy?")

    assert result == "refund policy?"


@pytest.mark.asyncio
async def test_rewrite_falls_back_to_original_on_llm_exception(rewriter, mock_llm_provider):
    mock_llm_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    result = await rewriter.rewrite("refund policy?")

    assert result == "refund policy?"
