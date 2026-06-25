import uuid
from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.process_query import ProcessQueryUseCase


@pytest.fixture
def pipeline():
    p = AsyncMock()

    async def _answer(query, user_id=None):
        yield {"type": "token", "content": "hi"}
        yield {"type": "done", "answer": "hi", "citations": []}

    p.answer = _answer
    return p


@pytest.mark.asyncio
async def test_execute_forwards_events_from_pipeline(pipeline):
    use_case = ProcessQueryUseCase(pipeline=pipeline)

    events = [event async for event in use_case.execute("query")]

    assert events == [
        {"type": "token", "content": "hi"},
        {"type": "done", "answer": "hi", "citations": []},
    ]


@pytest.mark.asyncio
async def test_execute_passes_user_id_through(pipeline):
    received = {}

    async def _answer(query, user_id=None):
        received["query"] = query
        received["user_id"] = user_id
        yield {"type": "done", "answer": "ok", "citations": []}

    pipeline.answer = _answer
    user_id = uuid.uuid4()
    use_case = ProcessQueryUseCase(pipeline=pipeline)

    _ = [event async for event in use_case.execute("my query", user_id=user_id)]

    assert received == {"query": "my query", "user_id": user_id}
