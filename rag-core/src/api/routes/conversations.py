from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel

from src.api.dependencies import get_conversation_repository
from src.governance.rbac import Principal, get_principal
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import access_denied

router = APIRouter()
logger = get_logger(__name__)


class ConversationOut(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[dict] = []
    model_used: str = ""
    latency_ms: int = 0
    trace_id: str = ""
    created_at: str


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = 50,
    principal: Principal = Depends(get_principal),
) -> list[ConversationOut]:
    """The signed-in user's recent conversations.

    Messages have been persisted since the governance work, but nothing could
    read them back — so a page refresh lost the user's history even though it
    was sitting in the database. This is the read side of that.

    Scoped to `principal.user_id` inside the query. A conversation holds the
    user's own questions and the answers they were given, so listing anyone
    else's is a disclosure in itself.
    """
    summaries = await get_conversation_repository().list_by_user(
        principal.user_id, limit=min(limit, 200)
    )
    return [
        ConversationOut(
            id=str(s.id),
            title=s.title,
            message_count=s.message_count,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
        )
        for s in summaries
    ]


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
async def get_conversation(
    conversation_id: str,
    principal: Principal = Depends(get_principal),
) -> list[MessageOut]:
    """Every message in one conversation, oldest first.

    Ownership is checked before any message is read, and a conversation
    belonging to someone else returns 404 rather than 403 — a 403 would
    confirm that a conversation with that id exists.
    """
    try:
        conversation_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="conversation_id is not a valid UUID.",
        ) from None

    repo = get_conversation_repository()
    if not await repo.owns(conversation_uuid, principal.user_id):
        access_denied.labels(reason="conversation_not_owned").inc()
        logger.warning(
            "conversation_access_denied",
            conversation_id=conversation_id,
            user_id=str(principal.user_id),
        )
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    messages = await repo.get_messages(conversation_uuid)
    return [
        MessageOut(
            id=str(m.id),
            role=m.role.value,
            content=m.content,
            citations=[
                {
                    "index": c.index,
                    "source_name": c.source_name,
                    "document_name": c.document_name,
                    "page_number": c.page_number,
                    "section": c.section,
                }
                for c in m.citations
            ],
            model_used=m.model_used,
            latency_ms=m.latency_ms,
            trace_id=m.langfuse_trace_id,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]
