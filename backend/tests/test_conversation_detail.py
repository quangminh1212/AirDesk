"""Regression tests for conversation serialization.

These exist because of a real bug: ``GET /agent/conversations/{id}`` returned a
500 whenever a message had tool calls attached. ``MessageRead`` declares a
``tool_calls`` field, so pydantic's ``from_attributes`` validation walked
``Message.tool_calls`` — a *lazy* relationship — and SQLAlchemy raised
``MissingGreenlet`` because the implicit IO happened outside the async context.

The endpoint now fetches tool calls explicitly and builds the schema from plain
values. These tests pin that behaviour so it cannot regress.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.agent import Conversation, Message, MessageRole, ToolCallRecord


async def _seed_conversation_with_tool_call(db_session) -> tuple[str, str]:
    """Create a conversation whose assistant message has one tool call."""
    convo = Conversation(channel="web")
    db_session.add(convo)
    await db_session.flush()

    msg = Message(
        conversation_id=convo.id,
        role=MessageRole.ASSISTANT,
        content="Đây là các chuyến bay phù hợp.",
        model="Claude-Fable.5",
        confidence=0.8,
        latency_ms=1234,
    )
    db_session.add(msg)
    await db_session.flush()

    db_session.add(
        ToolCallRecord(
            message_id=msg.id,
            tool_name="search_flights",
            arguments={"origin": "SGN", "destination": "HAN"},
            result={"count": 4},
            success=True,
            duration_ms=42,
        )
    )
    await db_session.commit()
    return convo.id, msg.id


@pytest.mark.asyncio
async def test_conversation_detail_with_tool_calls_does_not_500(
    client, auth_headers, db_session
) -> None:
    """The endpoint must serialize a message that has tool calls.

    Before the fix this raised MissingGreenlet -> HTTP 500.
    """
    convo_id, msg_id = await _seed_conversation_with_tool_call(db_session)

    resp = await client.get(f"/api/v1/agent/conversations/{convo_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["id"] == msg_id
    assert message["content"] == "Đây là các chuyến bay phù hợp."
    assert message["confidence"] == 0.8
    assert message["latency_ms"] == 1234

    assert len(message["tool_calls"]) == 1
    call = message["tool_calls"][0]
    assert call["tool_name"] == "search_flights"
    assert call["success"] is True
    assert call["duration_ms"] == 42
    assert call["arguments"] == {"origin": "SGN", "destination": "HAN"}


@pytest.mark.asyncio
async def test_conversation_detail_message_without_tools(client, auth_headers, db_session) -> None:
    """A message with no tool calls serializes to an empty list, not an error."""
    convo = Conversation(channel="web")
    db_session.add(convo)
    await db_session.flush()
    db_session.add(
        Message(
            conversation_id=convo.id,
            role=MessageRole.USER,
            content="Xin chào",
            latency_ms=0,
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/agent/conversations/{convo.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    message = resp.json()["messages"][0]
    assert message["tool_calls"] == []


@pytest.mark.asyncio
async def test_failed_tool_call_is_serialized(client, auth_headers, db_session) -> None:
    """A failed tool call keeps its error text, so the UI can show what broke."""
    convo = Conversation(channel="web")
    db_session.add(convo)
    await db_session.flush()
    msg = Message(
        conversation_id=convo.id,
        role=MessageRole.ASSISTANT,
        content="Tôi chưa lấy được dữ liệu.",
        latency_ms=10,
    )
    db_session.add(msg)
    await db_session.flush()
    db_session.add(
        ToolCallRecord(
            message_id=msg.id,
            tool_name="search_flights",
            arguments={},
            result=None,
            success=False,
            error="provider timeout",
            duration_ms=5000,
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/agent/conversations/{convo.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    call = resp.json()["messages"][0]["tool_calls"][0]
    assert call["success"] is False
    assert call["error"] == "provider timeout"


@pytest.mark.asyncio
async def test_conversation_detail_404_for_unknown_id(client, auth_headers) -> None:
    resp = await client.get("/api/v1/agent/conversations/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_messages_are_ordered_oldest_first(client, auth_headers, db_session) -> None:
    """The UI renders messages in reading order, so ordering must be stable."""
    convo = Conversation(channel="web")
    db_session.add(convo)
    await db_session.flush()
    for i in range(5):
        db_session.add(
            Message(
                conversation_id=convo.id,
                role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
                content=f"msg-{i}",
                latency_ms=0,
            )
        )
    await db_session.commit()

    resp = await client.get(f"/api/v1/agent/conversations/{convo.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    contents = [m["content"] for m in resp.json()["messages"]]
    assert contents == [f"msg-{i}" for i in range(5)]

    # Sanity: the DB really holds them.
    stored = (
        await db_session.scalars(select(Message).where(Message.conversation_id == convo.id))
    ).all()
    assert len(stored) == 5
