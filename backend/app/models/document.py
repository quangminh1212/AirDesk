"""Uploaded-document model: provenance, extraction state and structured data.

Every file a customer or operator uploads (ticket PDF, invoice, passport scan,
itinerary e-mail, …) is stored as one ``documents`` row. The raw bytes stay in
object storage keyed by ``sha256``; this table keeps the metadata, the
extracted plain text, and the LLM-produced structured payload so that:

* the same file is never processed (or billed) twice — ``sha256`` is the
  natural de-duplication key,
* the extraction state machine (``uploaded`` → ``extracting`` → ``extracted``
  / ``failed``) is queryable for background workers and operators,
* an operator can answer questions about a document without re-uploading it.
"""

from __future__ import annotations

import enum

from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class DocumentStatus(str, enum.Enum):
    """Lifecycle of an uploaded document."""

    UPLOADED = "uploaded"  # bytes stored, extraction not started
    EXTRACTING = "extracting"  # text/structured extraction in flight
    EXTRACTED = "extracted"  # text (and structured payload) available
    FAILED = "failed"  # extraction failed; see ``error``


class DocumentType(str, enum.Enum):
    """Business classification produced by the LLM extraction step."""

    AIRLINE_TICKET = "airline_ticket"
    INVOICE = "invoice"
    ITINERARY = "itinerary"
    PASSPORT = "passport"
    CONTRACT = "contract"
    PURCHASE_ORDER = "purchase_order"
    OTHER = "other"


class Document(UUIDMixin, TimestampMixin, Base):
    """A single uploaded file plus its extracted text and structured data."""

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_status_created", "status", "created_at"),)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Content hash used for de-duplication; two identical uploads share a row.
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=16),
        default=DocumentStatus.UPLOADED,
        nullable=False,
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False, length=24),
        default=DocumentType.OTHER,
        nullable=False,
    )

    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Validated ``ExtractedDocument`` payload (JSON) — never raw LLM output.
    extracted: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    #: True when the file yielded no text layer (scanned image / vector PDF).
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Document {self.filename} {self.doc_type.value} {self.status.value}>"
