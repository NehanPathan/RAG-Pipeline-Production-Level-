from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from src.governance.rbac import Principal
from src.retrieval.pipeline import QueryPipeline


class ProcessQueryUseCase:
    """Thin application-layer wrapper around QueryPipeline.answer() --
    keeps the /chat route from depending on the retrieval layer directly.
    """

    def __init__(self, pipeline: QueryPipeline) -> None:
        self._pipeline = pipeline

    async def execute(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        principal: Principal | None = None,
        history: Sequence[tuple[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        # `user_id` is kept for the tenant filter; `principal` carries the
        # clearance that bounds which classifications may be retrieved. When
        # a principal is supplied its user id is authoritative, since it was
        # resolved server-side rather than taken from the request body.
        effective_user_id = principal.user_id if principal else user_id
        async for event in self._pipeline.answer(
            query, user_id=effective_user_id, principal=principal, history=history
        ):
            yield event
