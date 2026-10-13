"""Document-vault schemas: upload results, listings and document Q&A."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, DocumentType


class DocumentRead(BaseModel):
    """One stored upload plus its extraction state and structured payload."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    doc_type: DocumentType
    confidence: float = 0.0
    #: True when the file had no text layer (scanned image / vector PDF).
    needs_ocr: bool = False
    error: str | None = None
    uploaded_by_id: str | None = None
    conversation_id: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentRead):
    """Detail view adds the plain text and the validated structured payload."""

    text_content: str | None = None
    extracted: dict[str, Any] | None = None


class DocumentAsk(BaseModel):
    """Question asked against one document's extracted text."""

    question: str = Field(..., min_length=2, max_length=1000)


class DocumentAnswer(BaseModel):
    """Answer grounded in the document text (no external calls are implied)."""

    answer: str
    document_id: str
    question: str


DocumentDetail.model_rebuild()
