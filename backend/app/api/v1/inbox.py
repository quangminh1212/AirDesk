"""Inbound mailbox endpoints: poll the mailbox and read what was stored.

The poll endpoint is deliberately thin: it runs exactly one
:class:`~app.services.email_ingest.EmailIngestService` cycle and reports the
counters back. All classification, routing and reply work lives in the service,
so the API cannot drift from the worker that polls the same mailbox.

An unconfigured mailbox (no ``IMAP_HOST``) is *not* an error: the endpoint
returns the zero counters with ``configured=false`` so a caller can render an
empty console instead of an error page.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import (
    CurrentUserDep,
    DbSession,
    Gateway,
    OperatorUser,
    Pagination,
)
from app.core.errors import NotFoundError
from app.models.email import EmailIntent, InboundEmail, InboundEmailStatus
from app.schemas.common import Page
from app.schemas.inbox import InboundEmailDetail, InboundEmailRead, PollSummary
from app.services.email_ingest import EmailIngestService, count_inbound

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.post("/poll", response_model=PollSummary, summary="Run one IMAP poll + ingest cycle")
async def poll_inbox(
    db: DbSession,
    operator: OperatorUser,
    gateway: Gateway,
    limit: int | None = Query(
        None, ge=1, le=200, description="Max messages to fetch (defaults to IMAP_MAX_MESSAGES)."
    ),
) -> PollSummary:
    """Fetch the UNSEEN mailbox and ingest every message once.

    The summary distinguishes the outcomes an operator cares about: freshly
    ``processed`` messages, ``skipped`` duplicates, ``failed`` messages needing a
    human, and mail that was stored but intentionally not answered.
    """
    service = EmailIngestService(db, gateway=gateway)
    return await service.poll_and_ingest(limit=limit)


@router.get(
    "/messages",
    response_model=Page[InboundEmailRead],
    summary="List stored inbound messages",
)
async def list_messages(
    db: DbSession,
    _user: CurrentUserDep,
    pagination: Pagination,
    status_filter: InboundEmailStatus | None = Query(None, alias="status"),
    intent: EmailIntent | None = Query(None),
) -> Page[InboundEmailRead]:
    """Newest first, optionally narrowed to one status and/or one intent."""
    page, page_size, offset = pagination

    stmt = select(InboundEmail).order_by(InboundEmail.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(InboundEmail.status == status_filter)
    if intent is not None:
        stmt = stmt.where(InboundEmail.intent == intent)

    total = await count_inbound(db, status=status_filter, intent=intent)
    rows = (await db.scalars(stmt.limit(page_size).offset(offset))).all()
    return Page.create(
        [InboundEmailRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/messages/{message_id}",
    response_model=InboundEmailDetail,
    summary="Get one stored message with its bodies",
)
async def get_message(db: DbSession, _user: CurrentUserDep, message_id: str) -> InboundEmailDetail:
    row = await db.get(InboundEmail, message_id)
    if row is None:
        raise NotFoundError("Inbound message not found.")
    return InboundEmailDetail.model_validate(row)


@router.get("/stats", summary="Counters per status (operator console header)")
async def inbox_stats(db: DbSession, _user: OperatorUser) -> dict[str, int]:
    """Total messages plus a per-status histogram, zero-filled for every status."""
    rows = (
        await db.execute(select(InboundEmail.status, func.count()).group_by(InboundEmail.status))
    ).all()
    counts = {row[0]: row[1] for row in rows}
    return {
        "total": sum(counts.values()),
        **{status.value: counts.get(status, 0) for status in InboundEmailStatus},
    }
