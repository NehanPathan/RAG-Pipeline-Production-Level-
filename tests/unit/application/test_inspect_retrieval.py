import uuid
from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.inspect_retrieval import InspectRetrievalUseCase
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.rbac import Principal, Role


@pytest.fixture
def pipeline():
    p = AsyncMock()
    p.inspect = AsyncMock(return_value="inspection-result")
    return p


@pytest.fixture
def analyst() -> Principal:
    return Principal(
        user_id=uuid.uuid4(), role=Role.ANALYST.value, clearance=Sensitivity.INTERNAL
    )


@pytest.mark.asyncio
async def test_execute_returns_pipeline_inspection_result(pipeline):
    use_case = InspectRetrievalUseCase(pipeline=pipeline)

    result = await use_case.execute("query")

    assert result == "inspection-result"
    pipeline.inspect.assert_called_once_with("query", user_id=None, principal=None)


@pytest.mark.asyncio
async def test_execute_passes_user_id_through(pipeline):
    user_id = uuid.uuid4()
    use_case = InspectRetrievalUseCase(pipeline=pipeline)

    await use_case.execute("query", user_id=user_id)

    pipeline.inspect.assert_called_once_with("query", user_id=user_id, principal=None)


@pytest.mark.asyncio
async def test_execute_passes_principal_through(pipeline, analyst):
    """The principal carries the clearance that bounds which classifications
    the retrieval may return, so it must reach the pipeline unmodified."""
    use_case = InspectRetrievalUseCase(pipeline=pipeline)

    await use_case.execute("query", user_id=analyst.user_id, principal=analyst)

    pipeline.inspect.assert_called_once_with(
        "query", user_id=analyst.user_id, principal=analyst
    )
