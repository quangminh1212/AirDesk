"""Agent conversation, message, tool-call and escalation models.

These tables are the backbone of the human-in-the-loop requirement:
every agent action is recorded, scored for confidence, and — when it is
low-confidence or above the auto-approval money limit — escalated to a human.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class Channel(str, enum.Enum):
    """Where the conversation originated."""

    WEB = "web"
    ZALO = "zalo"
    MESSENGER = "messenger"
    EMAIL = "email"
    API = "api"
    INTERNAL = "internal"


class ConversationStatus(str, enum.Enum):
    OPEN = "open"
    PENDING_HUMAN = "pending_human"  # escalated, waiting for a person
    RESOLVED = "resolved"
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class EscalationStatus(str, enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, native_enum=False, length=16), default=Channel.WEB, nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    customer_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False, length=20),
        default=ConversationStatus.OPEN,
        nullable=False,
    )
    assigned_to_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    escalations: Mapped[list[Escalation]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=12), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Token + cost accounting per message for observability.
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class ToolCallRecord(UUIDMixin, TimestampMixin, Base):
    """A record of a single tool/function invocation made by an agent."""

    __tablename__ = "tool_calls"

    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    message: Mapped[Message] = relationship(back_populates="tool_calls")


class Escalation(UUIDMixin, TimestampMixin, Base):
    """A hand-off from the agent to a human operator."""

    __tablename__ = "escalations"
    __table_args__ = (Index("ix_escalations_status_created", "status", "created_at"),)

    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    booking_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(EscalationStatus, native_enum=False, length=16),
        default=EscalationStatus.OPEN,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, default=2, nullable=False)  # 1 high .. 3 low
    assigned_to_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation | None] = relationship(back_populates="escalations")


class AuditLog(UUIDMixin, TimestampMixin, Base):
    """Append-only audit trail of every state-changing action."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity_created", "entity_type", "created_at"),)

    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
