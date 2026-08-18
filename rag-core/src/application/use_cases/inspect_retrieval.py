from __future__ import annotations

import uuid

from src.governance.rbac import Principal
from src.retrieval.pipeline import QueryPipeline, RetrievalInspection


class InspectRetrievalUseCase:
    """Thin application-layer wrapper around QueryPipeline.inspect() --
    keeps the /retrieval/inspect route from depending on the retrieval
    layer directly.
    """

    def __init__(self, pipeline: QueryPipeline) -> None:
        self._pipeline = pipeline

    async def execute(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        principal: Principal | None = None,
    ) -> RetrievalInspection:
        return await self._pipeline.inspect(query, user_id=user_id, principal=principal)
