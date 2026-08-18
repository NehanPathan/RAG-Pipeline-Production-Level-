from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.entities.conversation import Citation, Message, MessageRole, TokenUsage
from src.domain.repositories.conversation_repository import (
    ConversationRepository,
    ConversationSummary,
)
from src.infrastructure.database.postgres.models import ConversationModel, MessageModel
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class PostgresConversationRepository(ConversationRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_conversation(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, title: str = ""
    ) -> None:
        """Idempotent upsert.

        `on_conflict_do_nothing` rather than a select-then-insert: two
        messages from the same conversation can be written concurrently (a
        streamed answer and its follow-up), and the check-then-act version
        loses that race.
        """
        async with self._session_factory() as session:
            await session.execute(
                pg_insert(ConversationModel)
                .values(id=conversation_id, user_id=user_id, title=title[:500] or None)
                .on_conflict_do_nothing()
            )
            await session.commit()

    async def save_message(self, message: Message) -> Message:
        async with self._session_factory() as session:
            session.add(
                MessageModel(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    role=message.role.value,
                    content=message.content,
                    citations=[_citation_to_dict(c) for c in message.citations],
                    retrieved_chunks=message.retrieved_chunks,
                    model_used=message.model_used or None,
                    tokens_used={
                        "prompt_tokens": message.tokens_used.prompt_tokens,
                        "completion_tokens": message.tokens_used.completion_tokens,
                        "total_tokens": message.tokens_used.total_tokens,
                    },
                    latency_ms=message.latency_ms,
                    langfuse_trace_id=message.langfuse_trace_id or None,
                )
            )
            await session.commit()
        return message

    async def get_messages(self, conversation_id: uuid.UUID) -> list[Message]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MessageModel)
                .where(MessageModel.conversation_id == conversation_id)
                .order_by(MessageModel.created_at)
            )
            return [_to_entity(model) for model in result.scalars().all()]

    async def get_message(self, message_id: uuid.UUID) -> Message | None:
        async with self._session_factory() as session:
            model = await session.get(MessageModel, message_id)
            return _to_entity(model) if model else None

    async def list_by_user(self, user_id: uuid.UUID, limit: int = 50) -> list[ConversationSummary]:
        async with self._session_factory() as session:
            # One aggregate query rather than N+1: a sidebar showing 50
            # conversations must not issue 50 message queries.
            counts = (
                select(
                    MessageModel.conversation_id.label("cid"),
                    func.count(MessageModel.id).label("n"),
                    func.max(MessageModel.created_at).label("last_at"),
                )
                .group_by(MessageModel.conversation_id)
                .subquery()
            )
            result = await session.execute(
                select(
                    ConversationModel.id,
                    ConversationModel.title,
                    ConversationModel.created_at,
                    ConversationModel.updated_at,
                    func.coalesce(counts.c.n, 0),
                    counts.c.last_at,
                )
                .outerjoin(counts, counts.c.cid == ConversationModel.id)
                .where(
                    ConversationModel.user_id == user_id,
                    ConversationModel.is_active.is_(True),
                )
                .order_by(func.coalesce(counts.c.last_at, ConversationModel.created_at).desc())
                .limit(limit)
            )
            rows = result.all()

        return [
            ConversationSummary(
                id=row[0],
                title=row[1] or "Untitled conversation",
                created_at=row[2],
                updated_at=row[5] or row[3],
                message_count=int(row[4] or 0),
            )
            for row in rows
            # An empty conversation is created before the first answer is
            # persisted; showing it as a blank history entry is noise.
            if int(row[4] or 0) > 0
        ]

    async def owns(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ConversationModel.id).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            return result.first() is not None

    async def archive(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Flip `is_active` off, scoped to the owner in the same statement.

        `list_by_user` has always filtered on `is_active`, so this is the
        writer for a read path that already existed. Ownership is part of the
        WHERE clause rather than a preceding `owns()` call: with the check
        separate there is a window in which the conversation could change
        hands, and the update would apply to a row the caller no longer owns.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationModel)
                .where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.user_id == user_id,
                    ConversationModel.is_active.is_(True),
                )
                .values(is_active=False)
            )
            await session.commit()

        # rowcount is 0 both when the conversation does not exist and when it
        # belongs to someone else. The route turns both into a 404 so the two
        # stay indistinguishable to the caller.
        return bool(result.rowcount)

    async def get_question_for_answer(self, message_id: uuid.UUID) -> str | None:
        async with self._session_factory() as session:
            answer = await session.get(MessageModel, message_id)
            if answer is None:
                return None
            result = await session.execute(
                select(MessageModel.content)
                .where(
                    MessageModel.conversation_id == answer.conversation_id,
                    MessageModel.role == MessageRole.USER.value,
                    MessageModel.created_at <= answer.created_at,
                )
                # Ordered by time then id: a user message and its answer can
                # land on the same timestamp under a coarse clock, and
                # without the tiebreaker the "preceding" message would be
                # chosen non-deterministically between runs.
                .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
                .limit(1)
            )
            row = result.first()
            return row[0] if row else None


def _citation_to_dict(citation: Citation) -> dict:
    return {
        "index": citation.index,
        "source_name": citation.source_name,
        "document_name": citation.document_name,
        "chunk_id": str(citation.chunk_id),
        "document_id": str(citation.document_id) if citation.document_id else None,
        "page_number": citation.page_number,
        "section": citation.section,
    }


def _to_entity(model: MessageModel) -> Message:
    tokens = model.tokens_used or {}
    return Message(
        conversation_id=model.conversation_id,
        role=MessageRole(model.role),
        content=model.content,
        id=model.id,
        citations=[
            Citation(
                index=c.get("index", 0),
                source_name=c.get("source_name", ""),
                document_name=c.get("document_name", ""),
                chunk_id=uuid.UUID(c["chunk_id"]),
                page_number=c.get("page_number"),
                section=c.get("section"),
                document_id=uuid.UUID(c["document_id"]) if c.get("document_id") else None,
            )
            for c in (model.citations or [])
            # A citation row without a chunk_id predates citation validation
            # and cannot be resolved back to a source; skip rather than crash
            # the whole conversation read on one malformed entry.
            if c.get("chunk_id")
        ],
        retrieved_chunks=model.retrieved_chunks or [],
        model_used=model.model_used or "",
        tokens_used=TokenUsage(
            prompt_tokens=tokens.get("prompt_tokens", 0),
            completion_tokens=tokens.get("completion_tokens", 0),
            total_tokens=tokens.get("total_tokens", 0),
        ),
        latency_ms=model.latency_ms or 0,
        langfuse_trace_id=model.langfuse_trace_id or "",
        created_at=model.created_at,
    )
