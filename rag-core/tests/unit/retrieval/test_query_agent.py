import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.value_objects.metadata_filter import MetadataFilterSpec
from src.domain.value_objects.query_intent import IntentType, QueryIntent
from src.retrieval.agents.query_agent import QueryAgent


@pytest.fixture
def mock_rewriter():
    rewriter = AsyncMock()
    rewriter.rewrite = AsyncMock(return_value="rewritten query")
    return rewriter


@pytest.fixture
def mock_expander():
    expander = AsyncMock()
    expander.expand = AsyncMock(return_value=["variant one", "variant two"])
    return expander


@pytest.fixture
def mock_classifier():
    classifier = AsyncMock()
    classifier.classify = AsyncMock(
        return_value=QueryIntent(type=IntentType.POLICY_LOOKUP, confidence=0.9, domain="Operations")
    )
    return classifier


@pytest.fixture
def mock_source_selector():
    selector = MagicMock()
    selector.select = MagicMock(return_value=["Operations"])
    return selector


@pytest.fixture
def mock_filter_generator():
    generator = AsyncMock()
    generator.generate = AsyncMock(
        return_value=MetadataFilterSpec(domain="Operations", tags=["refund"])
    )
    return generator


@pytest.fixture
def agent(mock_rewriter, mock_expander, mock_classifier, mock_source_selector, mock_filter_generator):
    return QueryAgent(
        rewriter=mock_rewriter,
        expander=mock_expander,
        classifier=mock_classifier,
        source_selector=mock_source_selector,
        filter_generator=mock_filter_generator,
        expansion_count=2,
    )


@pytest.mark.asyncio
async def test_process_assembles_processed_query(agent):
    result = await agent.process("refund policy?", user_id=uuid.uuid4())

    assert result.original_query == "refund policy?"
    assert result.rewritten_query == "rewritten query"
    assert result.expanded_queries == ["variant one", "variant two"]
    assert result.intent.type == IntentType.POLICY_LOOKUP
    assert result.selected_sources == ["Operations"]
    assert result.filters.domain == "Operations"


@pytest.mark.asyncio
async def test_process_runs_steps_against_rewritten_query(
    agent, mock_expander, mock_classifier, mock_filter_generator
):
    await agent.process("original query")

    mock_expander.expand.assert_called_once_with("rewritten query", count=2)
    mock_classifier.classify.assert_called_once_with("rewritten query")
    mock_filter_generator.generate.assert_called_once()
    _, kwargs = mock_filter_generator.generate.call_args
    assert kwargs["query"] == "rewritten query"


@pytest.mark.asyncio
async def test_process_passes_classified_intent_to_source_selector(agent, mock_source_selector, mock_classifier):
    await agent.process("query")

    intent = mock_classifier.classify.return_value
    mock_source_selector.select.assert_called_once_with(intent)
