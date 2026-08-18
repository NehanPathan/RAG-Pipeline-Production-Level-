import uuid
from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.process_query import ProcessQueryUseCase
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.rbac import Principal, Role


@pytest.fixture
def pipeline():
    p = AsyncMock()

    async def _answer(query, user_id=None, principal=None, history=None):
        yield {"type": "token", "content": "hi"}
        yield {"type": "done", "answer": "hi", "citations": []}

    p.answer = _answer
    return p


@pytest.fixture
def analyst() -> Principal:
    return Principal(
        user_id=uuid.uuid4(), role=Role.ANALYST.value, clearance=Sensitivity.INTERNAL
    )


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

    async def _answer(query, user_id=None, principal=None, history=None):
        received.update(query=query, user_id=user_id, principal=principal)
        yield {"type": "done", "answer": "ok", "citations": []}

    pipeline.answer = _answer
    user_id = uuid.uuid4()
    use_case = ProcessQueryUseCase(pipeline=pipeline)

    _ = [event async for event in use_case.execute("my query", user_id=user_id)]

    assert received == {"query": "my query", "user_id": user_id, "principal": None}


@pytest.mark.asyncio
async def test_principal_user_id_overrides_the_supplied_one(pipeline, analyst):
    """A server-resolved principal is authoritative over a request-body
    user_id, which a caller could otherwise set to any tenant they liked."""
    received = {}

    async def _answer(query, user_id=None, principal=None, history=None):
        received.update(user_id=user_id, principal=principal)
        yield {"type": "done", "answer": "ok", "citations": []}

    pipeline.answer = _answer
    use_case = ProcessQueryUseCase(pipeline=pipeline)

    _ = [
        event
        async for event in use_case.execute(
            "q", user_id=uuid.uuid4(), principal=analyst
        )
    ]

    assert received["user_id"] == analyst.user_id
    assert received["principal"] is analyst


async def test_execute_forwards_conversation_history(pipeline):
    """A follow-up is only resolvable if the prior turns reach the rewriter.

    The use case is the seam between the chat route, which owns the
    conversation, and the pipeline, which owns the query. History passing
    through unchanged is the whole of its job here.
    """
    received = {}

    async def _answer(query, user_id=None, principal=None, history=None):
        received["history"] = history
        yield {"type": "done", "answer": "ok", "citations": []}

    pipeline.answer = _answer
    use_case = ProcessQueryUseCase(pipeline=pipeline)
    turns = [("user", "What is the revision of SSD09.0-02?"), ("assistant", "V1.1")]

    _ = [e async for e in use_case.execute("what about the bolts?", history=turns)]

    assert received["history"] == turns


async def test_execute_without_history_passes_none(pipeline):
    """The first turn of a conversation has nothing to resolve against."""
    received = {}

    async def _answer(query, user_id=None, principal=None, history=None):
        received["history"] = history
        yield {"type": "done", "answer": "ok", "citations": []}

    pipeline.answer = _answer
    use_case = ProcessQueryUseCase(pipeline=pipeline)

    _ = [e async for e in use_case.execute("first question")]

    assert received["history"] is None
