"""Inbox API tests: poll, list, filter, detail and RBAC.

Entirely offline. The poll endpoint is exercised with IMAP unconfigured (the
default in the test environment), which is the path a self-hosted deployment
without a mailbox takes — it must report zeros, not an error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.email import EmailIntent, InboundEmail, InboundEmailStatus
from app.models.user import User, UserRole


async def _token_headers(client, db_session, role: UserRole, email: str) -> dict[str, str]:
    """Create a user of ``role`` and return its bearer headers."""
    from app.core.security import hash_password

    user = User(
        email=email,
        full_name=f"Test {role.value}",
        hashed_password=hash_password("secret123"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _make_message(
    db_session,
    *,
    message_id: str = "<inbox-test-1@example.com>",
    subject: str = "Can I change my flight?",
    intent: EmailIntent = EmailIntent.BOOKING_CHANGE,
    status: InboundEmailStatus = InboundEmailStatus.RECEIVED,
) -> InboundEmail:
    row = InboundEmail(
        message_id=message_id,
        from_address="customer@example.com",
        to_address="support@airdesk.vn",
        subject=subject,
        body_text="Please move my flight to next week.",
        intent=intent,
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_poll_unconfigured_returns_zeros(client, auth_headers) -> None:
    """No IMAP host => a zero summary flagged ``configured: false``, never a 500."""
    settings = get_settings()
    monkeypatched = settings.imap_host
    settings.imap_host = None
    try:
        resp = await client.post("/api/v1/inbox/poll", headers=auth_headers)
    finally:
        settings.imap_host = monkeypatched

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is False
    assert body["fetched"] == 0
    assert body["processed"] == 0
    assert body["failed"] == 0
    assert body["skipped"] == 0


@pytest.mark.asyncio
async def test_list_messages_empty_page(client, auth_headers) -> None:
    resp = await client.get("/api/v1/inbox/messages", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["pages"] == 1


@pytest.mark.asyncio
async def test_list_and_detail_show_stored_message(client, auth_headers, db_session) -> None:
    row = await _make_message(db_session)

    listing = await client.get("/api/v1/inbox/messages", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [row.id]
    assert body["items"][0]["message_id"] == row.message_id
    assert body["items"][0]["intent"] == EmailIntent.BOOKING_CHANGE.value

    detail = await client.get(f"/api/v1/inbox/messages/{row.id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == row.id
    assert detail.json()["body_text"] == row.body_text
    assert detail.json()["status"] == InboundEmailStatus.RECEIVED.value


@pytest.mark.asyncio
async def test_list_filters_by_status_and_intent(client, auth_headers, db_session) -> None:
    wanted = await _make_message(
        db_session,
        message_id="<filter-wanted@example.com>",
        intent=EmailIntent.REFUND_REQUEST,
        status=InboundEmailStatus.PROCESSED,
    )
    await _make_message(
        db_session,
        message_id="<filter-other@example.com>",
        intent=EmailIntent.SPAM,
        status=InboundEmailStatus.IGNORED,
    )

    by_status = await client.get(
        "/api/v1/inbox/messages",
        headers=auth_headers,
        params={"status": InboundEmailStatus.PROCESSED.value},
    )
    assert by_status.status_code == 200, by_status.text
    assert by_status.json()["total"] == 1
    assert [i["id"] for i in by_status.json()["items"]] == [wanted.id]

    by_intent = await client.get(
        "/api/v1/inbox/messages",
        headers=auth_headers,
        params={"intent": EmailIntent.SPAM.value},
    )
    assert by_intent.status_code == 200, by_intent.text
    assert by_intent.json()["total"] == 1
    assert by_intent.json()["items"][0]["intent"] == EmailIntent.SPAM.value

    both = await client.get(
        "/api/v1/inbox/messages",
        headers=auth_headers,
        params={
            "status": InboundEmailStatus.IGNORED.value,
            "intent": EmailIntent.REFUND_REQUEST.value,
        },
    )
    assert both.status_code == 200, both.text
    assert both.json()["total"] == 0
    assert both.json()["items"] == []


@pytest.mark.asyncio
async def test_list_is_newest_first(client, auth_headers, db_session) -> None:
    older = await _make_message(db_session, message_id="<older@example.com>", subject="older")
    newer = await _make_message(db_session, message_id="<newer@example.com>", subject="newer")
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer.created_at = datetime(2026, 6, 1, tzinfo=UTC)
    await db_session.commit()

    resp = await client.get("/api/v1/inbox/messages", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()["items"]] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_detail_of_unknown_id_is_404(client, auth_headers) -> None:
    resp = await client.get(
        "/api/v1/inbox/messages/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_poll_requires_operator_role(client, db_session) -> None:
    """A viewer/agent token is rejected with 403; an operator is allowed."""
    viewer_headers = await _token_headers(
        client, db_session, UserRole.AGENT, "agent-inbox@airdesk.vn"
    )
    denied = await client.post("/api/v1/inbox/poll", headers=viewer_headers)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "forbidden"

    operator_headers = await _token_headers(
        client, db_session, UserRole.OPERATOR, "operator-inbox@airdesk.vn"
    )
    allowed = await client.post("/api/v1/inbox/poll", headers=operator_headers)
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_list_requires_auth(client) -> None:
    resp = await client.get("/api/v1/inbox/messages")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stored_row_is_queryable_by_message_id(db_session) -> None:
    """Sanity check on the fixture wiring the other inbox tests depend on."""
    await _make_message(db_session, message_id="<durable@example.com>")
    found = await db_session.scalar(
        select(InboundEmail).where(InboundEmail.message_id == "<durable@example.com>")
    )
    assert found is not None
    assert found.intent is EmailIntent.BOOKING_CHANGE


@pytest.mark.asyncio
async def test_inbox_stats_endpoint(client, auth_headers, db_session) -> None:
    await _make_message(db_session, status=InboundEmailStatus.PROCESSED)
    resp = await client.get("/api/v1/inbox/stats", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["processed"] == 1
    assert body["failed"] == 0


@pytest.mark.asyncio
async def test_operator_roles_cover_the_inbox(client, db_session) -> None:
    """Every operator-tier role satisfies the minimum-role dependency."""
    headers = await _token_headers(client, db_session, UserRole.OPERATOR, "ops2@airdesk.vn")
    resp = await client.get("/api/v1/inbox/messages", headers=headers)
    assert resp.status_code == 200
