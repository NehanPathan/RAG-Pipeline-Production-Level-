from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.domain.value_objects.provenance import Region


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Citation:
    index: int
    source_name: str
    document_name: str
    chunk_id: uuid.UUID
    page_number: int | None = None
    section: str | None = None
    # Completes the provenance chain answer -> chunk -> document. Without it
    # a citation identifies its source only by display name, which is neither
    # unique nor stable, and there is no way to invalidate cached answers
    # when the document behind them is deleted (see semantic cache purge).
    document_id: uuid.UUID | None = None
    # Where on the page the cited text sits. What turns a citation from "this
    # drawing says so" into "it says so *here*" -- on a detail sheet those are
    # very different claims, because the first cannot be checked.
    regions: list[Region] = field(default_factory=list)
    region_precision: str | None = None


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Message:
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    citations: list[Citation] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    model_used: str = ""
    tokens_used: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    langfuse_trace_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Conversation:
    user_id: uuid.UUID
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = ""
    is_active: bool = True
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = datetime.utcnow()
        if not self.title and message.role == MessageRole.USER:
            self.title = message.content[:100]
