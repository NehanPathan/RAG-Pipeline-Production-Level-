from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.dependencies import (
    get_conversation_repository,
    get_online_evaluator,
    get_query_pipeline,
)
from src.api.dependencies_rate_limit import rate_limit
from src.application.use_cases.process_query import ProcessQueryUseCase
from src.domain.entities.conversation import Citation, Message, MessageRole
from src.governance.rbac import Principal
from src.monitoring.logger import get_logger
from src.monitoring.tracing import get_current_trace_id

router = APIRouter()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    filters: dict = {}
    stream: bool = True
    debug: bool = False


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict] = []
    conversation_id: str
    message_id: str
    latency_ms: int
    trace_id: str
    refused: bool = False
    # Routing metadata. The streaming path emits these on the `done` event but
    # the JSON path used to drop them, so a non-streaming client could not tell
    # a cited document answer from ungrounded model knowledge -- exactly the
    # distinction the router exists to make visible.
    route: str = "rag"
    route_source: str = ""
    grounded: bool = True
    cached: bool = False
    refusal_reason: str | None = None


@router.post("/chat")
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(rate_limit("chat")),
):
    """
    Full pipeline (QueryAgent -> HybridRetriever -> Fuser -> Reranker ->
    ContextProcessor -> AnswerPipeline) via QueryPipeline, under governance.

    The caller's identity now comes from the resolved Principal rather than a
    client-supplied `user_id` field: the previous shape let any caller assert
    any tenant, which is not a filter so much as a suggestion. The principal's
    clearance is what bounds which classifications this query can retrieve.

    Both turns of the exchange are persisted before the response completes,
    which is what makes the answer auditable, rateable, and samplable for
    online evaluation. `filters` and `debug` remain accepted but unhonoured
    (see /retrieval/inspect for trace details).
    """
    conversation_id = _parse_uuid(request.conversation_id) or uuid.uuid4()
    trace_id = get_current_trace_id()

    logger.info(
        "chat_request",
        query=request.query[:100],
        conversation_id=str(conversation_id),
        clearance=principal.clearance.value,
    )
    if request.filters:
        logger.warning("chat_request_filters_ignored", filters=request.filters)

    if request.stream:
        return StreamingResponse(
            _stream_response(request.query, principal, conversation_id, trace_id, background_tasks),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Trace-Id": trace_id,
            },
        )

    return await _collect_response(
        request.query, principal, conversation_id, trace_id, background_tasks
    )


async def _stream_response(
    query: str,
    principal: Principal,
    conversation_id: uuid.UUID,
    trace_id: str,
    background_tasks: BackgroundTasks,
):
    use_case = ProcessQueryUseCase(pipeline=get_query_pipeline())
    start = time.time()

    answer = ""
    citations: list[dict] = []
    contexts: list[str] = []
    model_used = ""
    stored_query = query
    # Allocated before streaming so the client receives it on the `done`
    # event. Without an id the user can only submit feedback against a trace
    # id, and feedback with no message cannot be promoted into the golden
    # dataset later (the promotion step looks up the question via the message).
    message_id = uuid.uuid4()

    async for event in use_case.execute(query, principal=principal):
        if event["type"] == "done":
            event["latency_ms"] = int((time.time() - start) * 1000)
            event["message_id"] = str(message_id)
            event["conversation_id"] = str(conversation_id)
            answer = event.get("answer", "")
            citations = event.get("citations", [])
            model_used = event.get("model_used", "")
            stored_query = event.pop("question", stored_query)
            # Internal-only: consumed here, never sent to the client.
            contexts = event.pop("context_texts", [])
        yield f"data: {json.dumps(event)}\n\n"

    yield "data: [DONE]\n\n"

    # Persisted after the stream drains rather than inside the loop: the
    # answer is not complete until the final event, and a partial row would
    # be worse than none for both auditing and evaluation.
    await _persist_and_sample(
        query=stored_query,
        answer=answer,
        citations=citations,
        contexts=contexts,
        model_used=model_used,
        principal=principal,
        conversation_id=conversation_id,
        trace_id=trace_id,
        latency_ms=int((time.time() - start) * 1000),
        background_tasks=background_tasks,
        message_id=message_id,
    )


async def _collect_response(
    query: str,
    principal: Principal,
    conversation_id: uuid.UUID,
    trace_id: str,
    background_tasks: BackgroundTasks,
) -> ChatResponse:
    use_case = ProcessQueryUseCase(pipeline=get_query_pipeline())
    start = time.time()

    answer = ""
    citations: list[dict] = []
    contexts: list[str] = []
    model_used = ""
    stored_query = query
    refused = False
    meta: dict = {}

    async for event in use_case.execute(query, principal=principal):
        if event["type"] == "done":
            answer = event["answer"]
            citations = event["citations"]
            model_used = event.get("model_used", "")
            refused = bool(event.get("refused", False))
            stored_query = event.pop("question", stored_query)
            contexts = event.pop("context_texts", [])
            meta = event
        elif event["type"] == "error":
            answer = f"Error: {event['message']}"

    latency_ms = int((time.time() - start) * 1000)
    message_id = await _persist_and_sample(
        query=stored_query,
        answer=answer,
        citations=citations,
        contexts=contexts,
        model_used=model_used,
        principal=principal,
        conversation_id=conversation_id,
        trace_id=trace_id,
        latency_ms=latency_ms,
        background_tasks=background_tasks,
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        conversation_id=str(conversation_id),
        message_id=str(message_id),
        latency_ms=latency_ms,
        trace_id=trace_id,
        refused=refused,
        route=meta.get("route", "rag"),
        route_source=meta.get("route_source", ""),
        grounded=bool(meta.get("grounded", True)),
        cached=bool(meta.get("cached", False)),
        refusal_reason=meta.get("refusal_reason"),
    )


async def _persist_and_sample(
    *,
    query: str,
    answer: str,
    citations: list[dict],
    contexts: list[str],
    model_used: str,
    principal: Principal,
    conversation_id: uuid.UUID,
    trace_id: str,
    latency_ms: int,
    background_tasks: BackgroundTasks,
    message_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Write both turns, then queue the answer for online scoring.

    Persistence failures are logged but never surfaced: the user already has
    their answer, and failing the request after successfully answering it
    would trade a real outcome for a bookkeeping one. The `chat_persist_failed`
    log line is the signal that the audit trail has a hole in it.
    """
    repo = get_conversation_repository()
    # Reuse the id already sent to the client when there is one, so feedback
    # submitted against it resolves to a real row.
    assistant_message_id = message_id or uuid.uuid4()

    try:
        await repo.ensure_conversation(conversation_id, principal.user_id, title=query[:100])
        await repo.save_message(
            Message(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=query,
                langfuse_trace_id=trace_id,
            )
        )
        await repo.save_message(
            Message(
                id=assistant_message_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=answer,
                citations=[_to_citation(c) for c in citations],
                # The exact grounding text, stored so an incident review can
                # answer "what was the model actually looking at" without
                # needing to re-run a retrieval that may since have changed.
                retrieved_chunks=[{"content": text} for text in contexts],
                model_used=model_used,
                latency_ms=latency_ms,
                langfuse_trace_id=trace_id,
            )
        )
    except Exception as exc:
        logger.error("chat_persist_failed", trace_id=trace_id, error=str(exc))

    # MEASURE: sampled judge scoring runs in the background so it never adds
    # latency to the user's request. The sampler decides internally whether
    # this trace is in the sample.
    background_tasks.add_task(
        get_online_evaluator().maybe_score,
        trace_id=trace_id,
        question=query,
        answer=answer,
        contexts=contexts,
        citation_count=len(citations),
        message_id=assistant_message_id,
    )
    return assistant_message_id


def _to_citation(raw: dict) -> Citation:
    return Citation(
        index=raw.get("index", 0),
        source_name=raw.get("source_name", ""),
        document_name=raw.get("document_name", ""),
        chunk_id=uuid.UUID(raw["chunk_id"]),
        page_number=raw.get("page_number"),
        section=raw.get("section"),
        document_id=uuid.UUID(raw["document_id"]) if raw.get("document_id") else None,
    )


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        logger.warning("chat_conversation_id_malformed", value=raw[:64])
        return None
