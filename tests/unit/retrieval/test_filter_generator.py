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
        return_value=json.dumps(
            {"tags": ["refund", "policy"], "file_type": "pdf", "recency_days": None}
        )
    )
    user_id = uuid.uuid4()

    spec = await generator.generate(
        query="recent refund pdf policy",
        intent=intent,
        selected_sources=["Operations"],
        user_id=user_id,
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
async def test_generate_falls_back_to_minimal_filter_on_llm_exception(
    generator, mock_llm_provider, intent
):
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
async def test_generate_domain_is_none_when_no_sources_selected(
    generator, mock_llm_provider, intent
):
    mock_llm_provider.complete = AsyncMock(return_value=json.dumps({"tags": [], "file_type": None}))

    spec = await generator.generate(
        query="anything", intent=intent, selected_sources=[], user_id=None
    )

    assert spec.domain is None


#
# A generated tag is a guess about wording, and the model has no idea what the
# corpus is tagged with. Applied as a hard filter, a guess that matches
# nothing excludes everything: "what bolt-related information is present?"
# produced tags=[bolts, drawing], neither of which any chunk carried, so BM25
# returned zero rows from an index holding fourteen matches for the word. The
# answer was a refusal about a drawing that says `ALL BOLTS 3/4" DIA. A325`.


class _Vocabulary:
    def __init__(self, tags, fail=False):
        self._tags = tags
        self._fail = fail
        self.calls = 0

    async def aggregate_facets(self, fields, filters=None, max_values=50):
        self.calls += 1
        if self._fail:
            raise RuntimeError("elasticsearch unavailable")
        return {"tags": [(t, 1) for t in self._tags]}


@pytest.mark.asyncio
async def test_a_tag_the_corpus_does_not_use_is_dropped(mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(
        return_value='{"tags": ["bolts", "drawing"], "file_type": null}'
    )
    generator = FilterGenerator(
        mock_llm_provider, vocabulary=_Vocabulary(["structural steel", "welding"])
    )

    spec = await generator.generate("bolt information?", intent, ["Engineering"])

    assert spec.tags is None


@pytest.mark.asyncio
async def test_a_tag_the_corpus_does_use_is_kept(mock_llm_provider, intent):
    mock_llm_provider.complete = AsyncMock(return_value='{"tags": ["welding"]}')
    generator = FilterGenerator(
        mock_llm_provider, vocabulary=_Vocabulary(["structural steel", "welding"])
    )

    spec = await generator.generate("welding?", intent, ["Engineering"])

    assert spec.tags == ["welding"]


@pytest.mark.asyncio
async def test_the_vocabulary_is_cached_between_queries(mock_llm_provider, intent):
    """Every query would otherwise pay an aggregation for a value that
    changes only when documents are ingested."""
    mock_llm_provider.complete = AsyncMock(return_value='{"tags": ["welding"]}')
    vocabulary = _Vocabulary(["welding"])
    generator = FilterGenerator(mock_llm_provider, vocabulary=vocabulary)

    for _ in range(3):
        await generator.generate("welding?", intent, ["Engineering"])

    assert vocabulary.calls == 1


@pytest.mark.asyncio
async def test_an_unavailable_vocabulary_drops_tags_rather_than_guessing(mock_llm_provider, intent):
    """Unknown vocabulary means unvalidated tags. A broader search beats a
    silently empty one."""
    mock_llm_provider.complete = AsyncMock(return_value='{"tags": ["bolts"]}')
    generator = FilterGenerator(mock_llm_provider, vocabulary=_Vocabulary([], fail=True))

    spec = await generator.generate("bolts?", intent, ["Engineering"])

    assert spec.tags is None


@pytest.mark.asyncio
async def test_without_a_vocabulary_nothing_is_validated(mock_llm_provider, intent):
    """The check is an improvement where it can be made, not a new hard
    dependency for every caller."""
    mock_llm_provider.complete = AsyncMock(return_value='{"tags": ["bolts"]}')
    generator = FilterGenerator(mock_llm_provider)

    spec = await generator.generate("bolts?", intent, ["Engineering"])

    assert spec.tags == ["bolts"]


@pytest.mark.asyncio
async def test_a_tag_is_applied_with_the_corpus_spelling(mock_llm_provider, intent):
    """The model emits lowercase; the index stores `S-207`; a keyword filter
    is case-sensitive. Validating case-insensitively and then applying the
    lowercased form matches nothing -- the exact silent empty-result failure
    this validation exists to prevent, reintroduced one layer down."""
    mock_llm_provider.complete = AsyncMock(return_value='{"tags": ["s-207"]}')
    generator = FilterGenerator(mock_llm_provider, vocabulary=_Vocabulary(["S-207", "ISMB 300"]))

    spec = await generator.generate("what is on s-207?", intent, ["Engineering"])

    assert spec.tags == ["S-207"]
