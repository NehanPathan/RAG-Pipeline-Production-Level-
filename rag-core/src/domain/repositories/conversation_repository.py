from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.domain.entities.conversation import Message


@dataclass
class ConversationSummary:
    """A conversation as it appears in a history list.

    Deliberately not the full `Conversation` entity: rendering a sidebar must
    not require loading every message of every conversation.
    """

    id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    last_message: str = ""


class ConversationRepository(ABC):
    """Persistence for conversations and the messages inside them.

    Exists because an AI system that cannot show what it told a user cannot
    be governed: feedback has nothing to attach to, online evaluation has no
    population to sample, and an incident review has no record to reconstruct.
    Phase 3's design review listed the missing repository as Gap 8; the
    governance work is what finally makes it mandatory rather than nice to
    have.
    """

    @abstractmethod
    async def ensure_conversation(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, title: str = ""
    ) -> None:
        """Create the conversation row if it does not already exist."""
        ...

    @abstractmethod
    async def save_message(self, message: Message) -> Message: ...

    @abstractmethod
    async def get_messages(self, conversation_id: uuid.UUID) -> list[Message]: ...

    @abstractmethod
    async def list_by_user(
        self, user_id: uuid.UUID, limit: int = 50
    ) -> list[ConversationSummary]:
        """Recent conversations belonging to one user.

        Scoped by `user_id` in the query rather than filtered afterwards: a
        conversation contains the user's questions and the system's answers,
        so listing someone else's is a disclosure in itself.
        """
        ...

    @abstractmethod
    async def owns(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Whether this user owns the conversation — checked before any read."""
        ...

    @abstractmethod
    async def get_message(self, message_id: uuid.UUID) -> Message | None: ...

    @abstractmethod
    async def get_question_for_answer(self, message_id: uuid.UUID) -> str | None:
        """The user turn that produced the given assistant message.

        Needed by the feedback loop: a thumbs-down names the *answer*, but
        the golden dataset needs the *question*. Resolved by walking back to
        the preceding user message rather than storing a second copy of the
        question on the feedback row, so the two can never disagree.
        """
        ...
