"""Inbound email model: every message pulled from a mailbox is recorded.

The row is the durable receipt of an inbound message and the hand-off point to
a human: a message that could not be classified, routed or answered is left
with ``status=failed`` and a human-readable ``error`` so an operator can pick
it up in the inbox console instead of losing it silently.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.agent import Conversation


class EmailIntent(str, enum.Enum):
    """What the customer's message is about, as decided by the classifier."""

    FLIGHT_BOOKING = "flight_booking"
    BOOKING_CHANGE = "booking_change"
    REFUND_REQUEST = "refund_request"
    INVOICE_OR_DOCUMENT = "invoice_or_document"
    COMPLAINT = "complaint"
    GENERAL_INQUIRY = "general_inquiry"
    SPAM = "spam"
    UNKNOWN = "unknown"


class InboundEmailStatus(str, enum.Enum):
    """Processing lifecycle of one stored message."""

    RECEIVED = "received"  # stored, not routed yet
    PROCESSING = "processing"  # agent is working on it
    PROCESSED = "processed"  # reply generated (and sent, when configured)
    FAILED = "failed"  # needs a human; see ``error``
    IGNORED = "ignored"  # spam / not a customer request


class InboundEmail(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "inbound_emails"
    __table_args__ = (
        Index("ix_inbound_emails_status_created", "status", "created_at"),
        Index("ix_inbound_emails_intent_created", "intent", "created_at"),
    )

    #: RFC5322 Message-ID; the de-duplication key for repeated polls.
    message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    to_address: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    intent: Mapped[EmailIntent] = mapped_column(
        Enum(EmailIntent, native_enum=False, length=24),
        default=EmailIntent.UNKNOWN,
        nullable=False,
        index=True,
    )
    status: Mapped[InboundEmailStatus] = mapped_column(
        Enum(InboundEmailStatus, native_enum=False, length=16),
        default=InboundEmailStatus.RECEIVED,
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Populated on failure so a human can triage without digging through logs.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Headers, attachments metadata and the reply that was produced.
    raw_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped[Conversation | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InboundEmail {self.message_id} {self.intent.value} {self.status.value}>"
