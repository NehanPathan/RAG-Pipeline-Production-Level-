import uuid
from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.inspect_retrieval import InspectRetrievalUseCase


@pytest.fixture
def pipeline():
    p = AsyncMock()
    p.inspect = AsyncMock(return_value="inspection-result")
    return p


@pytest.mark.asyncio
async def test_execute_returns_pipeline_inspection_result(pipeline):
    use_case = InspectRetrievalUseCase(pipeline=pipeline)

    result = await use_case.execute("query")

    assert result == "inspection-result"
    pipeline.inspect.assert_called_once_with("query", user_id=None)


@pytest.mark.asyncio
async def test_execute_passes_user_id_through(pipeline):
    user_id = uuid.uuid4()
    use_case = InspectRetrievalUseCase(pipeline=pipeline)

    await use_case.execute("query", user_id=user_id)

    pipeline.inspect.assert_called_once_with("query", user_id=user_id)
