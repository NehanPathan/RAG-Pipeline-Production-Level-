from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from src.retrieval.pipeline import QueryPipeline


class ProcessQueryUseCase:
    """Thin application-layer wrapper around QueryPipeline.answer() --
    keeps the /chat route from depending on the retrieval layer directly.
    """

    def __init__(self, pipeline: QueryPipeline) -> None:
        self._pipeline = pipeline

    async def execute(self, query: str, user_id: uuid.UUID | None = None) -> AsyncIterator[dict]:
        async for event in self._pipeline.answer(query, user_id=user_id):
            yield event
