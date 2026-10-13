"""The AirDesk conversational agent.

A tool-using agent loop built on the LiteLLM gateway. For each user turn it:
  1. persists the user message,
  2. runs a bounded ReAct loop (LLM -> tool calls -> observations),
  3. records every tool call with timing and outcome,
  4. scores its own confidence and escalates to a human when low,
  5. persists the assistant reply and returns a structured ``AgentReply``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.qa import QAEvaluator, QASubject, Severity
from app.agents.tools import TOOL_SPECS, ToolRuntime
from app.core.config import get_settings
from app.core.llm import LiteLLMGateway
from app.core.logging import get_logger
from app.models.agent import (
    Channel,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    ToolCallRecord,
)
from app.schemas.booking import AgentReply

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are AirDesk, an expert AI travel agent specialising in Vietnamese \
airlines (Vietnam Airlines, VietJet Air, Bamboo Airways, Vietravel Airlines, Pacific Airlines) \
and the wider regional market. You help customers search flights, compare fares, and complete \
bookings through the tools provided.

Enterprise principles:
- Always confirm route, dates, and passenger count before searching.
- Never invent flight prices or availability; only report what tools return.
- Before creating a booking, restate the itinerary and total price and ask for confirmation.
- Present prices in VND with thousands separators (e.g. 2,450,000 VND).
- If a request is ambiguous, out of policy, or you are not confident, call \
`escalate_to_human` with a clear reason rather than guessing.
- Be concise, professional, and answer in the customer's language (Vietnamese or English).

You have tools for searching flights, pricing offers, creating bookings, listing providers, \
and escalating to a human. Use them.
"""

MAX_TOOL_ITERATIONS = 6

# Minimum time (seconds) to reserve for the QA pass when the tool loop has
# already consumed most of the request budget.
_QA_SEVERITY_ESCALATE = {Severity.ERROR, Severity.CRITICAL}


class FlightAgent:
    """Orchestrates one full conversation turn."""

    def __init__(
        self,
        db: AsyncSession,
        gateway: LiteLLMGateway,
        evaluator: QAEvaluator | None = None,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._settings = get_settings()
        self._evaluator = evaluator or QAEvaluator()

    async def _get_or_create_conversation(
        self, conversation_id: str | None, channel: str, customer_ref: str | None
    ) -> Conversation:
        if conversation_id:
            convo = await self._db.get(Conversation, conversation_id)
            if convo:
                return convo
        convo = Conversation(
            channel=Channel(channel) if channel in Channel._value2member_map_ else Channel.WEB,
            status=ConversationStatus.OPEN,
            customer_ref=customer_ref,
        )
        self._db.add(convo)
        await self._db.flush()
        return convo

    async def _history(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        from sqlalchemy import select

        rows = (
            await self._db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        ).all()
        messages: list[dict[str, Any]] = []
        for m in reversed(rows):
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT) and m.content:
                messages.append({"role": m.role.value, "content": m.content})
        return messages

    async def handle(
        self,
        *,
        user_message: str,
        conversation_id: str | None = None,
        channel: str = "web",
        customer_ref: str | None = None,
        actor_id: str | None = None,
    ) -> AgentReply:
        convo = await self._get_or_create_conversation(conversation_id, channel, customer_ref)

        user_msg = Message(conversation_id=convo.id, role=MessageRole.USER, content=user_message)
        self._db.add(user_msg)
        await self._db.flush()

        history = await self._history(convo.id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": user_message},
        ]

        runtime = ToolRuntime(self._db, conversation_id=convo.id, actor_id=actor_id)
        tool_records: list[dict[str, Any]] = []
        offers: list[dict[str, Any]] = []
        booking: dict[str, Any] | None = None
        escalated = False
        escalation_reason: str | None = None
        intent: str | None = None

        final_content = ""
        total_prompt = total_completion = 0
        total_cost = 0.0
        last_confidence: float | None = None
        started = time.perf_counter()

        for _ in range(MAX_TOOL_ITERATIONS):
            result = await self._gateway.complete(
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                metadata={"conversation_id": convo.id},
            )
            total_prompt += result.prompt_tokens
            total_completion += result.completion_tokens
            total_cost += result.cost_usd

            if not result.tool_calls:
                final_content = result.content
                break

            # Record the assistant's tool-call message.
            assistant_msg = Message(
                conversation_id=convo.id,
                role=MessageRole.ASSISTANT,
                content=result.content or "",
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
            )
            self._db.add(assistant_msg)
            await self._db.flush()

            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in result.tool_calls
                    ],
                }
            )

            for tc in result.tool_calls:
                name = tc["name"]
                args = tc["arguments"] or {}
                if name == "search_flights":
                    intent = "search_flights"
                elif name == "create_booking":
                    intent = "create_booking"
                elif name == "escalate_to_human":
                    intent = "escalation"

                outcome = await runtime.run(name, args)
                record = ToolCallRecord(
                    message_id=assistant_msg.id,
                    tool_name=name,
                    arguments=args,
                    result=_safe_json(outcome.result),
                    success=outcome.success,
                    error=outcome.error,
                    duration_ms=outcome.duration_ms,
                )
                self._db.add(record)

                if name == "search_flights" and outcome.success:
                    offers = outcome.result.get("offers", [])
                if name == "create_booking" and outcome.success:
                    booking = outcome.result
                if name == "escalate_to_human" and outcome.success:
                    escalated = True
                    escalation_reason = args.get("reason")

                tool_records.append(
                    {
                        "tool_name": name,
                        "arguments": args,
                        "success": outcome.success,
                        "duration_ms": outcome.duration_ms,
                        "error": outcome.error,
                        "result": _safe_json(outcome.result),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": name,
                        "content": json.dumps(_safe_json(outcome.result), ensure_ascii=False),
                    }
                )

        latency_ms = int((time.perf_counter() - started) * 1000)
        last_confidence = self._score_confidence(
            escalated=escalated, booking=booking, offers=offers, tool_records=tool_records
        )

        reply_text = final_content or _fallback_reply(escalated, booking)

        # --- automated quality review ------------------------------------
        # Runs before the reply is persisted so a failing turn can still be
        # routed to a human instead of reaching the customer unreviewed.
        qa_report = self._evaluator.evaluate(
            QASubject(
                reply=reply_text,
                user_message=user_message,
                intent=intent,
                confidence=last_confidence,
                tool_calls=tool_records,
                offers=offers,
                booking=booking,
                escalated=escalated,
                conversation_id=convo.id,
            )
        )

        # --- auto-escalation ----------------------------------------------
        needs_escalation = last_confidence < self._settings.agent_confidence_threshold
        qa_blocked = qa_report.max_severity in _QA_SEVERITY_ESCALATE

        if not escalated and (needs_escalation or qa_blocked):
            escalated = True
            escalation_reason = "low_confidence" if needs_escalation else "qa_failed"
            details: dict[str, Any] = {"confidence": last_confidence}
            if qa_blocked:
                details["qa_findings"] = qa_report.codes
            try:
                await runtime.run(
                    "escalate_to_human",
                    {
                        "reason": escalation_reason,
                        "priority": 1 if qa_blocked else 2,
                        "details": details,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto_escalation_failed", error=str(exc))

        if escalated:
            convo.status = ConversationStatus.PENDING_HUMAN

        assistant_reply = Message(
            conversation_id=convo.id,
            role=MessageRole.ASSISTANT,
            content=reply_text,
            model=self._settings.llm_model,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            cost_usd=total_cost,
            latency_ms=latency_ms,
            confidence=last_confidence,
            meta={"qa": qa_report.to_dict()},
        )
        self._db.add(assistant_reply)
        await self._db.commit()

        return AgentReply(
            conversation_id=convo.id,
            reply=assistant_reply.content,
            intent=intent,
            confidence=last_confidence,
            offers=offers,
            booking=None,
            escalated=escalated,
            escalation_reason=escalation_reason,
            tool_calls=tool_records,
            qa=qa_report.to_dict(),
        )

    @staticmethod
    def _score_confidence(
        *,
        escalated: bool,
        booking: dict[str, Any] | None,
        offers: list[dict[str, Any]],
        tool_records: list[dict[str, Any]],
    ) -> float:
        """Heuristic self-assessment used to decide on human hand-off."""
        if escalated:
            return 0.3
        score = 0.6
        if offers:
            score += 0.2
        if booking:
            score += 0.15
        failures = sum(1 for r in tool_records if not r["success"])
        score -= 0.15 * failures
        if not tool_records:
            score = 0.55
        return max(0.0, min(1.0, score))


def _safe_json(value: Any) -> Any:
    """Coerce a value into JSON-serialisable form for storage."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


def _fallback_reply(escalated: bool, booking: dict[str, Any] | None) -> str:
    if booking:
        return (
            f"Đã tạo đặt chỗ {booking.get('reference')} "
            f"({booking.get('status')}). Vui lòng kiểm tra email xác nhận."
        )
    if escalated:
        return "Yêu cầu của bạn đã được chuyển cho nhân viên hỗ trợ, vui lòng chờ trong giây lát."
    return "Xin lỗi, tôi chưa thể xử lý yêu cầu này. Vui lòng cung cấp thêm thông tin."
