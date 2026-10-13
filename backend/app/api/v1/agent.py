"""Agent conversation endpoints: chat, history, escalations."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.agents.runner import FlightAgent
from app.api.deps import CurrentUserDep, DbSession, Gateway, OperatorUser, Pagination
from app.core.errors import NotFoundError
from app.models.agent import Conversation, Escalation, EscalationStatus, Message, ToolCallRecord
from app.schemas.booking import (
    AgentReply,
    ConversationDetail,
    ConversationRead,
    EscalationRead,
    EscalationResolve,
    MessageRead,
    ToolCallRead,
)
from app.schemas.common import Page
from app.schemas.flight import TripRequest
from app.services.audit import record_audit

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentReply, summary="Chat with the flight agent")
async def chat(
    db: DbSession, user: CurrentUserDep, gateway: Gateway, payload: TripRequest
) -> AgentReply:
    agent = FlightAgent(db, gateway)
    return await agent.handle(
        user_message=payload.message,
        conversation_id=payload.conversation_id,
        channel=payload.channel,
        actor_id=user.id,
    )


@router.get("/conversations", response_model=Page[ConversationRead], summary="List conversations")
async def list_conversations(
    db: DbSession, _user: CurrentUserDep, pagination: Pagination
) -> Page[ConversationRead]:
    page, page_size, offset = pagination
    from sqlalchemy import func

    total = await db.scalar(select(func.count()).select_from(Conversation)) or 0
    rows = (
        await db.scalars(
            select(Conversation)
            .order_by(Conversation.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
    ).all()
    return Page.create(
        [ConversationRead.model_validate(c) for c in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get conversation with messages and tool calls",
)
async def get_conversation(
    db: DbSession, _user: CurrentUserDep, conversation_id: str
) -> ConversationDetail:
    convo = await db.get(Conversation, conversation_id)
    if convo is None:
        raise NotFoundError("Conversation not found.")

    messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
    ).all()

    # Build each MessageRead from an explicit dict rather than via
    # model_validate(m): validating the ORM object directly would make pydantic
    # traverse `Message.tool_calls`, a lazy relationship, which raises
    # MissingGreenlet because the implicit IO happens outside the async context.
    # We already fetched the tool calls explicitly, so pass them in.
    msg_reads: list[MessageRead] = []
    for m in messages:
        tool_calls = (
            await db.scalars(select(ToolCallRecord).where(ToolCallRecord.message_id == m.id))
        ).all()
        msg_reads.append(
            MessageRead(
                id=m.id,
                role=m.role,
                content=m.content,
                model=m.model,
                confidence=m.confidence,
                latency_ms=m.latency_ms,
                created_at=m.created_at,
                tool_calls=[ToolCallRead.model_validate(t) for t in tool_calls],
            )
        )

    # Same reasoning as above: ConversationDetail declares `messages`, so
    # validating the ORM object would traverse `Conversation.messages` and hit
    # the identical MissingGreenlet. Build it from plain values instead.
    detail = ConversationDetail(
        id=convo.id,
        channel=convo.channel,
        status=convo.status,
        customer_ref=convo.customer_ref,
        summary=convo.summary,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        messages=msg_reads,
    )
    return detail


@router.get("/escalations", response_model=Page[EscalationRead], summary="List escalations (queue)")
async def list_escalations(
    db: DbSession,
    _user: OperatorUser,
    pagination: Pagination,
    status_filter: EscalationStatus | None = None,
) -> Page[EscalationRead]:
    page, page_size, offset = pagination
    from sqlalchemy import func

    stmt = select(Escalation).order_by(Escalation.priority.asc(), Escalation.created_at.asc())
    count_stmt = select(func.count()).select_from(Escalation)
    if status_filter:
        stmt = stmt.where(Escalation.status == status_filter)
        count_stmt = count_stmt.where(Escalation.status == status_filter)
    total = await db.scalar(count_stmt) or 0
    rows = (await db.scalars(stmt.limit(page_size).offset(offset))).all()
    return Page.create(
        [EscalationRead.model_validate(e) for e in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/escalations/{escalation_id}/resolve",
    response_model=EscalationRead,
    status_code=status.HTTP_200_OK,
    summary="Resolve or reject an escalation (operator)",
)
async def resolve_escalation(
    db: DbSession,
    operator: OperatorUser,
    escalation_id: str,
    payload: EscalationResolve,
) -> EscalationRead:
    from datetime import UTC, datetime

    esc = await db.get(Escalation, escalation_id)
    if esc is None:
        raise NotFoundError("Escalation not found.")
    esc.status = (
        EscalationStatus.RESOLVED if payload.action == "resolve" else EscalationStatus.REJECTED
    )
    esc.resolution_note = payload.resolution_note
    esc.assigned_to_id = operator.id
    esc.resolved_at = datetime.now(UTC)
    await record_audit(
        db,
        action=f"escalation.{payload.action}",
        entity_type="escalation",
        entity_id=esc.id,
        actor_id=operator.id,
        after={"note": payload.resolution_note},
    )
    await db.commit()
    await db.refresh(esc)
    return EscalationRead.model_validate(esc)
