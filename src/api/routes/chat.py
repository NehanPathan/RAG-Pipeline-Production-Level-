from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.dependencies import get_query_pipeline
from src.application.use_cases.process_query import ProcessQueryUseCase
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    # Stand-in for the user identity that real auth middleware (Phase 7,
    # not yet built) will derive from a JWT. Used for the multi-tenant
    # user_id filter at the vector/search layer (see Module B's fix).
    user_id: str | None = None
    filters: dict = {}
    stream: bool = True
    debug: bool = False


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict] = []
    conversation_id: str
    message_id: str
    latency_ms: int


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Full pipeline (QueryAgent -> HybridRetriever -> Fuser -> Reranker ->
    ContextProcessor -> AnswerPipeline) via QueryPipeline. Streams via SSE
    when request.stream=True (default); otherwise drains the stream and
    returns the final answer as JSON.

    `filters` and `debug` are accepted for API-contract compatibility but
    not yet honored: Module A's FilterGenerator already auto-derives
    metadata filters from the query, and merging in a client-supplied
    override is a follow-up, not part of this wiring. `debug` would attach
    retrieval trace info inline -- use POST /retrieval/inspect for that today.
    Conversation persistence is intentionally not wired here either -- see
    Phase 3 design review Gap 8 (no ConversationRepository exists yet).
    """
    conversation_id = request.conversation_id or str(uuid.uuid4())
    logger.info("chat_request", query=request.query[:100], conversation_id=conversation_id)
    if request.filters:
        logger.warning("chat_request_filters_ignored", filters=request.filters)

    if request.stream:
        return StreamingResponse(
            _stream_response(request.query, request.user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return await _collect_response(request.query, request.user_id, conversation_id)


async def _stream_response(query: str, user_id: str | None):
    use_case = ProcessQueryUseCase(pipeline=get_query_pipeline())
    start = time.time()
    user_uuid = uuid.UUID(user_id) if user_id else None

    async for event in use_case.execute(query, user_id=user_uuid):
        if event["type"] == "done":
            event["latency_ms"] = int((time.time() - start) * 1000)
        yield f"data: {json.dumps(event)}\n\n"

    yield "data: [DONE]\n\n"


async def _collect_response(query: str, user_id: str | None, conversation_id: str) -> ChatResponse:
    use_case = ProcessQueryUseCase(pipeline=get_query_pipeline())
    start = time.time()
    user_uuid = uuid.UUID(user_id) if user_id else None

    answer = ""
    citations: list[dict] = []
    async for event in use_case.execute(query, user_id=user_uuid):
        if event["type"] == "done":
            answer = event["answer"]
            citations = event["citations"]
        elif event["type"] == "error":
            answer = f"Error: {event['message']}"

    return ChatResponse(
        answer=answer,
        citations=citations,
        conversation_id=conversation_id,
        message_id=str(uuid.uuid4()),
        latency_ms=int((time.time() - start) * 1000),
    )
