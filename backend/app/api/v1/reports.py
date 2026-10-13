"""Reporting / analytics endpoints for the operator dashboard.

Portability note
----------------
Production runs on Postgres and the test suite runs on SQLite, so this module
never relies on ``date_trunc`` / ``date()``. Instead it fetches the grouping
columns (``created_at`` plus the few scalar metrics) and buckets them **in
Python**, which behaves identically on both dialects. The bucket helpers
(``_bucket_series``, ``_day_range``) are pure functions, so they are also easy
to unit-test without a database.

All aggregations use SQLAlchemy ``select(func.count())`` / ``func.avg`` /
``func.sum`` with ``group_by`` where the grouping key is a real column.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, OperatorUser
from app.models.agent import (
    Conversation,
    ConversationStatus,
    Escalation,
    EscalationStatus,
    Message,
    MessageRole,
    ToolCallRecord,
)
from app.models.booking import Booking
from app.models.task import CLOSED_TASK_STATUSES, Task
from app.schemas.report import (
    AgentPerformancePoint,
    AgentPerformanceReport,
    AgentQualityMetrics,
    BookingMetrics,
    ConversationMetrics,
    DocumentMetrics,
    EscalationMetrics,
    MessageMetrics,
    OverviewReport,
    TaskMetrics,
    VolumePoint,
    VolumeReport,
)

router = APIRouter(prefix="/reports", tags=["reports"])

#: Statuses that count as "the human still has to do something about this".
_OPEN_ESCALATION_STATUSES: tuple[EscalationStatus, ...] = (
    EscalationStatus.OPEN,
    EscalationStatus.CLAIMED,
)


def _coerce_datetime(value: datetime | str) -> datetime:
    """Normalise a DB timestamp to an aware UTC ``datetime``.

    SQLite hands back naive datetimes for ``DateTime(timezone=True)`` columns;
    Postgres hands back aware ones. Both must land in the same bucket.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _day_range(days: int, *, now: datetime | None = None) -> list[date]:
    """Return ``days`` consecutive dates ending today (UTC), oldest first."""
    today = (now or _utcnow()).date()
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _counts_by_day(
    rows: Sequence[tuple[datetime | str, ...]], keys: Sequence[date]
) -> dict[date, int]:
    """Count rows per UTC day, zero-filling every key in ``keys``."""
    counts = dict.fromkeys(keys, 0)
    allowed = set(keys)
    for row in rows:
        key = _coerce_datetime(row[0]).date()
        if key in allowed:
            counts[key] += 1
    return counts


def _mean(values: Sequence[float | int]) -> float | None:
    """Arithmetic mean, or ``None`` for an empty sample."""
    return round(sum(values) / len(values), 6) if values else None


async def _scalar_counts(db: DbSession, column: Any, model: Any) -> dict[Any, int]:
    """``GROUP BY`` a column and return ``{value: count}`` (aggregation style used repo-wide)."""
    stmt = select(column, func.count()).select_from(model).group_by(column)
    rows = (await db.execute(stmt)).all()
    return {row[0]: row[1] for row in rows}


@router.get("/overview", response_model=OverviewReport, summary="Dashboard overview")
async def overview(db: DbSession, _user: OperatorUser) -> OverviewReport:
    now = _utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    # --- conversations ----------------------------------------------------
    conversation_status = await _scalar_counts(db, Conversation.status, Conversation)

    # --- messages ---------------------------------------------------------
    message_total = await db.scalar(select(func.count()).select_from(Message)) or 0
    messages_24h = (
        await db.scalar(
            select(func.count()).select_from(Message).where(Message.created_at >= day_ago)
        )
        or 0
    )
    messages_7d = (
        await db.scalar(
            select(func.count()).select_from(Message).where(Message.created_at >= week_ago)
        )
        or 0
    )

    # --- bookings ---------------------------------------------------------
    booking_status = await _scalar_counts(db, Booking.status, Booking)
    revenue_rows = (
        await db.execute(
            select(Booking.currency, func.sum(Booking.total_amount)).group_by(Booking.currency)
        )
    ).all()

    # --- escalations ------------------------------------------------------
    escalation_total = await db.scalar(select(func.count()).select_from(Escalation)) or 0
    escalations_open = (
        await db.scalar(
            select(func.count())
            .select_from(Escalation)
            .where(Escalation.status.in_(_OPEN_ESCALATION_STATUSES))
        )
        or 0
    )
    resolution_rows = (
        await db.execute(
            select(Escalation.created_at, Escalation.resolved_at).where(
                Escalation.resolved_at.is_not(None)
            )
        )
    ).all()
    resolution_minutes = [
        (_coerce_datetime(resolved) - _coerce_datetime(created)).total_seconds() / 60.0
        for created, resolved in resolution_rows
        if resolved is not None
    ]

    # --- agent quality ----------------------------------------------------
    quality_row = (
        await db.execute(
            select(
                func.avg(Message.confidence),
                func.avg(Message.latency_ms),
                func.sum(Message.cost_usd),
            ).where(Message.role == MessageRole.ASSISTANT)
        )
    ).one()
    tool_total = await db.scalar(select(func.count()).select_from(ToolCallRecord)) or 0
    tool_failed = (
        await db.scalar(
            select(func.count())
            .select_from(ToolCallRecord)
            .where(ToolCallRecord.success.is_(False))
        )
        or 0
    )

    # --- tasks ------------------------------------------------------------
    tasks_open = (
        await db.scalar(
            select(func.count()).select_from(Task).where(Task.status.not_in(CLOSED_TASK_STATUSES))
        )
        or 0
    )
    tasks_overdue = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.due_at.is_not(None),
                Task.due_at < now,
                Task.status.not_in(CLOSED_TASK_STATUSES),
            )
        )
        or 0
    )
    tasks_done_week = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.completed_at.is_not(None), Task.completed_at >= week_ago)
        )
        or 0
    )

    # --- documents --------------------------------------------------------
    # ``Task.document_id`` is the only document reference the schema carries,
    # so the vault is measured over the tasks that point at one and typed by
    # the ``doc_type`` recorded in their ``meta`` payload.
    documents_total = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.document_id.is_not(None), Task.document_id != "")
        )
        or 0
    )
    documents_by_type = await _scalar_counts(db, Task.task_type, Task)

    return OverviewReport(
        generated_at=now.isoformat(),
        conversations=ConversationMetrics(
            total=sum(conversation_status.values()),
            open=conversation_status.get(ConversationStatus.OPEN, 0),
            pending_human=conversation_status.get(ConversationStatus.PENDING_HUMAN, 0),
            resolved=conversation_status.get(ConversationStatus.RESOLVED, 0),
        ),
        messages=MessageMetrics(total=message_total, last_24h=messages_24h, last_7d=messages_7d),
        bookings=BookingMetrics(
            total=sum(booking_status.values()),
            by_status={status.value: count for status, count in booking_status.items()},
            revenue_by_currency={
                currency or "": int(total or 0) for currency, total in revenue_rows
            },
        ),
        escalations=EscalationMetrics(
            total=escalation_total,
            open=escalations_open,
            avg_resolution_minutes=_mean(resolution_minutes),
        ),
        agent_quality=AgentQualityMetrics(
            avg_confidence=round(float(quality_row[0]), 6) if quality_row[0] is not None else None,
            avg_latency_ms=round(float(quality_row[1]), 6) if quality_row[1] is not None else None,
            total_cost_usd=round(float(quality_row[2] or 0.0), 6),
            tool_success_rate=(round(1.0 - (tool_failed / tool_total), 6) if tool_total else None),
            tool_calls_total=tool_total,
            tool_calls_failed=tool_failed,
        ),
        tasks=TaskMetrics(open=tasks_open, overdue=tasks_overdue, done_this_week=tasks_done_week),
        documents=DocumentMetrics(
            total=documents_total,
            by_doc_type={task_type.value: count for task_type, count in documents_by_type.items()},
        ),
    )


@router.get("/volume", response_model=VolumeReport, summary="Daily volume time series")
async def volume(
    db: DbSession,
    _user: OperatorUser,
    days: int = Query(30, ge=1, le=365, description="Number of days in the series."),
) -> VolumeReport:
    keys = _day_range(days)
    start = datetime.combine(keys[0], datetime.min.time(), tzinfo=UTC)

    conversations = (
        await db.execute(select(Conversation.created_at).where(Conversation.created_at >= start))
    ).all()
    messages = (
        await db.execute(select(Message.created_at).where(Message.created_at >= start))
    ).all()
    bookings = (
        await db.execute(select(Booking.created_at).where(Booking.created_at >= start))
    ).all()
    escalations = (
        await db.execute(select(Escalation.created_at).where(Escalation.created_at >= start))
    ).all()

    conversation_counts = _counts_by_day(conversations, keys)
    message_counts = _counts_by_day(messages, keys)
    booking_counts = _counts_by_day(bookings, keys)
    escalation_counts = _counts_by_day(escalations, keys)

    return VolumeReport(
        days=days,
        start=keys[0],
        end=keys[-1],
        points=[
            VolumePoint(
                date=key,
                conversations_created=conversation_counts[key],
                messages_created=message_counts[key],
                bookings_created=booking_counts[key],
                escalations_created=escalation_counts[key],
            )
            for key in keys
        ],
    )


@router.get(
    "/agent-performance",
    response_model=AgentPerformanceReport,
    summary="Daily agent quality time series",
)
async def agent_performance(
    db: DbSession,
    _user: OperatorUser,
    days: int = Query(30, ge=1, le=365, description="Number of days in the series."),
) -> AgentPerformanceReport:
    keys = _day_range(days)
    start = datetime.combine(keys[0], datetime.min.time(), tzinfo=UTC)

    turn_rows = (
        await db.execute(
            select(Message.created_at, Message.confidence, Message.latency_ms, Message.cost_usd)
            .where(Message.role == MessageRole.ASSISTANT, Message.created_at >= start)
            .order_by(Message.created_at.asc())
        )
    ).all()
    tool_rows = (
        await db.execute(
            select(Message.created_at, ToolCallRecord.success)
            .join(Message, ToolCallRecord.message_id == Message.id)
            .where(ToolCallRecord.created_at >= start)
        )
    ).all()
    escalation_rows = (
        await db.execute(select(Escalation.created_at).where(Escalation.created_at >= start))
    ).all()

    turns: dict[date, list[tuple[float | None, int | None, float | None]]] = {
        key: [] for key in keys
    }
    allowed = set(keys)
    for created_at, confidence, latency_ms, cost_usd in turn_rows:
        key = _coerce_datetime(created_at).date()
        if key in allowed:
            turns[key].append((confidence, latency_ms, cost_usd))

    tools: dict[date, list[bool]] = {key: [] for key in keys}
    for created_at, success in tool_rows:
        key = _coerce_datetime(created_at).date()
        if key in allowed:
            tools[key].append(bool(success))

    escalations = _counts_by_day(escalation_rows, keys)

    points: list[AgentPerformancePoint] = []
    for key in keys:
        day_turns = turns[key]
        day_tools = tools[key]
        confidences = [c for c, _latency, _cost in day_turns if c is not None]
        latencies = [latency for _c, latency, _cost in day_turns if latency is not None]
        costs = [cost or 0.0 for _c, _latency, cost in day_turns]
        turn_count = len(day_turns)
        failed_tools = sum(1 for ok in day_tools if not ok)
        points.append(
            AgentPerformancePoint(
                date=key,
                turns=turn_count,
                avg_confidence=_mean(confidences),
                escalation_rate=round(escalations[key] / turn_count, 6) if turn_count else 0.0,
                avg_latency_ms=_mean(latencies),
                total_cost_usd=round(sum(costs), 6),
                tool_failure_rate=(round(failed_tools / len(day_tools), 6) if day_tools else 0.0),
                tool_calls_total=len(day_tools),
                tool_calls_failed=failed_tools,
            )
        )

    return AgentPerformanceReport(days=days, start=keys[0], end=keys[-1], points=points)
