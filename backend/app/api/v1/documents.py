"""Document-vault endpoints: upload, list, inspect, ask and delete.

The upload path is the only place that spends money (LLM extraction), so it is
built around two cheap guards before any model call:

* **size** — the bytes are read once and rejected when they exceed
  ``settings.document_max_bytes``; nothing is persisted.
* **de-duplication** — ``sha256`` is the natural key. Re-uploading identical
  bytes returns 200 with the *existing* row, never a second extraction.

Extraction itself can fail for a hundred reasons (corrupt PDF, unreadable scan,
provider outage). A failure is recorded on the row as ``status=failed`` with a
human-readable ``error`` — it must never surface as a 500, because the bytes are
already safely stored and re-extraction is cheap to trigger later.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from sqlalchemy import ColumnElement, func, select

from app.api.deps import (
    CurrentUserDep,
    DbSession,
    Gateway,
    OperatorUser,
    Pagination,
)
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus, DocumentType
from app.schemas.common import Page
from app.schemas.document import DocumentAnswer, DocumentAsk, DocumentDetail, DocumentRead
from app.services.audit import record_audit
from app.services.documents import (
    ALLOWED_CONTENT_TYPES,
    answer_about_document,
    extract_structured,
    extract_text,
    sha256_of,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

#: Extension -> MIME fallback for clients that send ``application/octet-stream``.
_EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".log": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

#: Characters that must never reach a prompt (a NUL truncates most parsers).
_UNSAFE_FILENAME_CHARS = "\x00\r\n"


def _extension(filename: str) -> str:
    """Lower-cased file extension including the dot, or ``""``."""
    _, _, tail = filename.rpartition(".")
    if not tail or "." not in filename:
        return ""
    return f".{tail.lower()}"


def _resolve_content_type(content_type: str, filename: str) -> tuple[str, str | None]:
    """Return ``(effective_type, error)`` for one upload.

    The client-declared type wins when it is allow-listed. A generic
    ``application/octet-stream`` (or a missing type) is resolved from the file
    extension so a plain ``curl -F`` upload still works.
    """
    allowed = set(ALLOWED_CONTENT_TYPES) | set(get_settings().document_allowed_types_list)
    declared = (content_type or "").split(";")[0].strip().lower()

    if declared and declared != "application/octet-stream" and declared in allowed:
        return declared, None

    from_extension = _EXTENSION_TYPES.get(_extension(filename), "")
    if from_extension and from_extension in allowed:
        return from_extension, None

    return (declared or ""), (
        f"Unsupported content type {declared or '(none)'!r}. Allowed: {', '.join(sorted(allowed))}."
    )


def _clean_filename(filename: str | None) -> str:
    """Trim control characters and clamp to the column width."""
    cleaned = (filename or "upload").translate({ord(c): None for c in _UNSAFE_FILENAME_CHARS})
    return (cleaned.strip() or "upload")[:255]


async def _load_or_404(db: DbSession, document_id: str) -> Document:
    document = await db.get(Document, document_id)
    if document is None:
        raise NotFoundError("Document not found.")
    return document


@router.post(
    "",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document and extract its contents",
    responses={
        200: {"description": "Identical bytes already stored; the existing row is returned."},
        413: {"description": "The upload exceeds DOCUMENT_MAX_BYTES."},
        415: {"description": "The content type is not allow-listed."},
    },
)
async def upload_document(
    db: DbSession,
    user: CurrentUserDep,
    gateway: Gateway,
    response: Response,
    file: Annotated[UploadFile, File(...)],
) -> DocumentDetail:
    """Store one file, then extract text and structured fields from it.

    Returns ``201`` for a new document and ``200`` when the same bytes were
    uploaded before (``sha256`` de-duplication), so a retried upload is free.
    """
    settings = get_settings()
    filename = _clean_filename(file.filename)
    content_type, type_error = _resolve_content_type(file.content_type or "", filename)
    if type_error is not None:
        raise ValidationError(type_error)

    data = await file.read()
    size = len(data)
    if size == 0:
        raise ValidationError("The uploaded file is empty.")
    if size > settings.document_max_bytes:
        raise ValidationError(
            f"File is {size} bytes, which exceeds the {settings.document_max_bytes}-byte limit."
        )

    digest: str = sha256_of(data)
    existing = await db.scalar(select(Document).where(Document.sha256 == digest))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return DocumentDetail.model_validate(existing)

    # Reject before a row exists: an unreadable *format* is the caller's fault,
    # whereas a failed *read* of a supported format is recorded on the row.
    text_result = extract_text(filename, content_type, data)
    if text_result.error:
        raise ValidationError(f"Could not read {filename!r}: {text_result.error}")

    document = Document(
        filename=filename,
        content_type=content_type,
        size_bytes=size,
        sha256=digest,
        status=DocumentStatus.EXTRACTING,
        doc_type=DocumentType.OTHER,
        uploaded_by_id=user.id,
        meta={"filename": filename, "content_type": content_type},
    )
    db.add(document)
    await db.flush()

    try:
        document.text_content = text_result.truncated_text()
        document.needs_ocr = text_result.needs_ocr
        if text_result.page_count is not None:
            document.meta = {**(document.meta or {}), "page_count": text_result.page_count}

        structured = await extract_structured(
            document.text_content or "", gateway=gateway, filename=filename
        )
        document.extracted = structured.model_dump(mode="json")
        document.doc_type = structured.doc_type
        document.confidence = structured.confidence
        document.status = DocumentStatus.EXTRACTED
        document.error = None
    except Exception as exc:  # noqa: BLE001 - extraction failure is data, not a 500
        logger.warning("document_extraction_failed", document_id=document.id, error=str(exc))
        document.status = DocumentStatus.FAILED
        document.error = f"{type(exc).__name__}: {exc}"[:4000]

    await record_audit(
        db,
        action="document.uploaded",
        entity_type="document",
        entity_id=document.id,
        actor_id=user.id,
        after={
            "filename": document.filename,
            "sha256": document.sha256,
            "size_bytes": document.size_bytes,
            "status": document.status.value,
        },
    )
    await db.commit()
    await db.refresh(document)
    return DocumentDetail.model_validate(document)


@router.get("", response_model=Page[DocumentRead], summary="List uploaded documents")
async def list_documents(
    db: DbSession,
    _user: CurrentUserDep,
    pagination: Pagination,
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    doc_type: DocumentType | None = Query(None),
) -> Page[DocumentRead]:
    """Newest first, optionally narrowed by extraction status and/or type."""
    page, page_size, offset = pagination

    filters: list[ColumnElement[bool]] = []
    if status_filter is not None:
        filters.append(Document.status == status_filter)
    if doc_type is not None:
        filters.append(Document.doc_type == doc_type)

    stmt = select(Document)
    count_stmt = select(func.count()).select_from(Document)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = await db.scalar(count_stmt) or 0
    rows = (
        await db.scalars(stmt.order_by(Document.created_at.desc()).limit(page_size).offset(offset))
    ).all()
    return Page.create(
        [DocumentRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentDetail, summary="Get one document")
async def get_document(db: DbSession, _user: CurrentUserDep, document_id: str) -> DocumentDetail:
    return DocumentDetail.model_validate(await _load_or_404(db, document_id))


@router.post(
    "/{document_id}/ask",
    response_model=DocumentAnswer,
    summary="Ask a question about a stored document",
)
async def ask_document(
    db: DbSession,
    _user: CurrentUserDep,
    gateway: Gateway,
    document_id: str,
    payload: DocumentAsk,
) -> DocumentAnswer:
    """Answer a question from the document's extracted text (no re-upload)."""
    document = await _load_or_404(db, document_id)
    text = (document.text_content or "").strip()
    if not text:
        raise ConflictError(
            "This document has no extracted text yet; wait for extraction or re-upload it."
        )
    answer = await answer_about_document(payload.question, text, gateway=gateway)
    return DocumentAnswer(answer=answer, document_id=document.id, question=payload.question)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (operator)",
)
async def delete_document(db: DbSession, operator: OperatorUser, document_id: str) -> Response:
    """Hard-delete one document row; the raw bytes live outside the DB."""
    document = await _load_or_404(db, document_id)
    await record_audit(
        db,
        action="document.deleted",
        entity_type="document",
        entity_id=document.id,
        actor_id=operator.id,
        after={"filename": document.filename, "sha256": document.sha256},
    )
    await db.delete(document)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
