"""Quality-assurance endpoints.

Exposes the automated turn reviews produced by ``app.agents.qa`` so an
operator can find failing agent turns, inspect *why* they failed, and act on
them. This is the read side of the "verify results, record failures, hand the
work to a person" requirement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.agents.qa import QAEvaluator, QASubject, Verdict
from app.api.deps import CurrentUserDep, DbSession, OperatorUser, Pagination
from app.core.errors import NotFoundError
from app.models.agent import Conversation, ConversationStatus, Message, MessageRole, ToolCallRecord
from app.schemas.common import Page
from app.schemas.qa import QAFindingRead, QAOverview, QATurnRead

router = APIRouter(prefix="/qa", tags=["quality"])


async def _load_turn(db: DbSession, message: Message) -> QATurnRead:
    """Re-evaluate one assistant message into a report."""
    tool_rows = (
        await db.scalars(select(ToolCallRecord).where(ToolCallRecord.message_id == message.id))
    ).all()
    tool_calls: list[dict[str, Any]] = [
        {
            "tool": t.tool_name,
            "success": t.success,
            "error": t.error,
            "duration_ms": t.duration_ms,
        }
        for t in tool_rows
    ]
    stored = (message.meta or {}).get("qa") if message.meta else None

    if stored:
        verdict = Verdict(stored.get("verdict", "pass"))
        score = float(stored.get("score", 1.0))
        findings = [
            QAFindingRead(
                code=f.get("code", ""),
                message=f.get("message", ""),
                severity=f.get("severity", "info"),
                details=f.get("details") or {},
            )
            for f in stored.get("findings", [])
        ]
    else:
        # Older turns predate QA storage: evaluate them on the fly.
        convo = await db.get(Conversation, message.conversation_id)
        report = QAEvaluator().evaluate(
            QASubject(
                reply=message.content or "",
                user_message="",
                confidence=message.confidence,
                tool_calls=tool_calls,
                escalated=convo.status is ConversationStatus.PENDING_HUMAN if convo else False,
                conversation_id=message.conversation_id,
            )
        )
        verdict, score = report.verdict, report.score
        findings = [QAFindingRead(**f.to_dict()) for f in report.findings]

    return QATurnRead(
        message_id=message.id,
        conversation_id=message.conversation_id,
        created_at=message.created_at,
        verdict=verdict.value,
        score=score,
        confidence=message.confidence,
        latency_ms=message.latency_ms,
        model=message.model,
        cost_usd=message.cost_usd,
        content_preview=(message.content or "")[:300],
        findings=findings,
        tool_calls=tool_calls,
    )


@router.get("/turns", response_model=Page[QATurnRead], summary="Review agent turns")
async def list_turns(
    db: DbSession,
    _user: CurrentUserDep,
    pagination: Pagination,
    verdict: Verdict | None = None,
    days: int = 7,
) -> Page[QATurnRead]:
    """List assistant turns, newest first, optionally filtered by verdict.

    Filtering by verdict is done in Python because the verdict lives inside a
    JSON column; the window is bounded by ``days`` to keep that scan small.
    """
    page, page_size, offset = pagination
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 90)))

    base = (
        select(Message)
        .where(
            Message.role == MessageRole.ASSISTANT,
            Message.created_at >= since,
        )
        .order_by(Message.created_at.desc())
    )

    if verdict is None:
        total = (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.role == MessageRole.ASSISTANT, Message.created_at >= since)
            )
            or 0
        )
        rows = (await db.scalars(base.limit(page_size).offset(offset))).all()
        items = [await _load_turn(db, m) for m in rows]
        return Page.create(items, total=total, page=page, page_size=page_size)

    # Filtered path: scan the window, then paginate in memory.
    rows = (await db.scalars(base.limit(500))).all()
    turns = [await _load_turn(db, m) for m in rows]
    matching = [t for t in turns if t.verdict == verdict.value]
    return Page.create(
        matching[offset : offset + page_size],
        total=len(matching),
        page=page,
        page_size=page_size,
    )


@router.get("/turns/{message_id}", response_model=QATurnRead, summary="Inspect one agent turn")
async def get_turn(db: DbSession, _user: CurrentUserDep, message_id: str) -> QATurnRead:
    message = await db.get(Message, message_id)
    if message is None or message.role is not MessageRole.ASSISTANT:
        raise NotFoundError("Assistant message not found.")
    return await _load_turn(db, message)


@router.get("/overview", response_model=QAOverview, summary="QA health summary (operator)")
async def qa_overview(db: DbSession, _user: OperatorUser, days: int = 7) -> QAOverview:
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 90)))
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.role == MessageRole.ASSISTANT, Message.created_at >= since)
            .order_by(Message.created_at.desc())
            .limit(500)
        )
    ).all()

    turns = [await _load_turn(db, m) for m in rows]
    counts: dict[str, int] = {}
    codes: dict[str, int] = {}
    for turn in turns:
        counts[turn.verdict] = counts.get(turn.verdict, 0) + 1
        for f in turn.findings:
            codes[f.code] = codes.get(f.code, 0) + 1

    total = len(turns)
    failed = counts.get(Verdict.FAIL.value, 0)
    reviewed = counts.get(Verdict.REVIEW.value, 0)
    passed = counts.get(Verdict.PASS.value, 0)

    return QAOverview(
        window_days=days,
        total_turns=total,
        passed=passed,
        review=reviewed,
        failed=failed,
        pass_rate=round(passed / total, 4) if total else 1.0,
        avg_score=round(sum(t.score for t in turns) / total, 4) if total else 1.0,
        avg_confidence=(
            round(
                sum(t.confidence for t in turns if t.confidence is not None)
                / max(1, sum(1 for t in turns if t.confidence is not None)),
                4,
            )
            if any(t.confidence is not None for t in turns)
            else None
        ),
        top_findings=dict(sorted(codes.items(), key=lambda kv: kv[1], reverse=True)[:10]),
    )
