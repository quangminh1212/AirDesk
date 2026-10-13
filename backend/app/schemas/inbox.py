"""Inbound mailbox schemas: stored messages and poll summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.email import EmailIntent, InboundEmailStatus


class InboundEmailRead(BaseModel):
    """One stored inbound message, as shown in the inbox console."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    from_address: str
    to_address: str
    subject: str
    intent: EmailIntent
    status: InboundEmailStatus
    conversation_id: str | None = None
    error: str | None = None
    processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    #: Headers/attachment metadata; raw bodies are served by the detail route.
    raw_meta: dict[str, Any] | None = None

    @property
    def attachments(self) -> list[dict[str, Any]]:
        """Attachment metadata recorded on this message (never the bytes)."""
        return list((self.raw_meta or {}).get("attachments") or [])


class InboundEmailDetail(InboundEmailRead):
    """Detail view adds the extracted text bodies."""

    body_text: str = ""
    body_html: str = ""
    reply_text: str | None = None


class PollSummary(BaseModel):
    """Outcome of a single IMAP poll + ingest cycle."""

    fetched: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    ignored: int = 0
    configured: bool = True
    errors: list[str] = Field(default_factory=list)
