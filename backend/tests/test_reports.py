"""Reporting / analytics coverage.

``app/api/v1/reports.py`` is the operator-dashboard surface: it aggregates
conversations, messages, bookings, escalations, tasks and agent quality. Until
now it was only exercised by the live ``scripts/e2e_check.py`` harness, which is
excluded from pytest — so a regression here could reach production without a
single hermetic test failing.

Two layers are tested deliberately:

* **Pure helpers** (``_day_range``, ``_counts_by_day``, ``_coerce_datetime``,
  ``_mean``) — these exist precisely to keep the module portable across
  Postgres and SQLite, so their behaviour is pinned directly, including the
  zero-filling and naive/aware timestamp coercion that the portability hinges on.
* **HTTP endpoints** — asserted against a real request so the response schema,
  auth requirement and bucketing wiring are all covered end to end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.v1.reports import (
    _coerce_datetime,
    _counts_by_day,
    _day_range,
    _mean,
)
from app.models.agent import Conversation, ConversationStatus, Message, MessageRole

# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_day_range_is_consecutive_and_ends_today() -> None:
    """``days`` keys, oldest first, ending on "today" for the injected clock."""
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    keys = _day_range(3, now=now)

    assert keys == [
        date(2026, 3, 8),
        date(2026, 3, 9),
        date(2026, 3, 10),
    ]
    # strictly increasing by one day — no gaps, no duplicates
    assert all(b - a == timedelta(days=1) for a, b in zip(keys, keys[1:], strict=False))


def test_day_range_single_day() -> None:
    """A one-day window is just today, not an empty list."""
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    assert _day_range(1, now=now) == [date(2026, 3, 10)]


def test_coerce_datetime_treats_naive_as_utc() -> None:
    """SQLite returns naive datetimes; they must become aware UTC.

    If they stayed naive this would raise on comparison against an aware value
    instead of silently landing in the wrong bucket.
    """
    coerced = _coerce_datetime(datetime(2026, 3, 10, 8, 30))

    assert coerced.tzinfo is not None
    assert coerced.utcoffset() == timedelta(0)
    assert coerced == datetime(2026, 3, 10, 8, 30, tzinfo=UTC)


def test_coerce_datetime_accepts_iso_string() -> None:
    """Postgres drivers can hand back a string; it must round-trip."""
    coerced = _coerce_datetime("2026-03-10T08:30:00+00:00")

    assert coerced == datetime(2026, 3, 10, 8, 30, tzinfo=UTC)


def test_coerce_datetime_preserves_explicit_offset() -> None:
    """An aware non-UTC value keeps its instant (not its wall clock)."""
    vn_tz = timedelta(hours=7)  # UTC+7, the agency's operating timezone
    coerced = _coerce_datetime(datetime(2026, 3, 10, 15, 30, tzinfo=timezone(vn_tz)))

    assert coerced == datetime(2026, 3, 10, 8, 30, tzinfo=UTC)


def test_counts_by_day_zero_fills_every_key() -> None:
    """Days with no rows must appear as ``0``, not be missing.

    A missing key would make the chart skip that day and misrepresent volume.
    """
    keys = [date(2026, 3, 8), date(2026, 3, 9), date(2026, 3, 10)]

    counts = _counts_by_day([], keys)

    assert counts == {date(2026, 3, 8): 0, date(2026, 3, 9): 0, date(2026, 3, 10): 0}


def test_counts_by_day_groups_and_ignores_out_of_range() -> None:
    """Rows outside the window are dropped; in-window rows are grouped by day."""
    keys = [date(2026, 3, 9), date(2026, 3, 10)]
    rows = [
        (datetime(2026, 3, 9, 1, 0),),
        (datetime(2026, 3, 9, 23, 59),),
        (datetime(2026, 3, 10, 0, 0),),
        (datetime(2026, 2, 1, 0, 0),),  # before the window -> ignored
        (datetime(2026, 4, 1, 0, 0),),  # after the window  -> ignored
    ]

    counts = _counts_by_day(rows, keys)

    assert counts[date(2026, 3, 9)] == 2
    assert counts[date(2026, 3, 10)] == 1
    assert sum(counts.values()) == 3


def test_counts_by_day_boundary_is_day_of_utc_instant() -> None:
    """Bucketing uses the UTC date of the instant, at both day edges."""
    keys = [date(2026, 3, 9), date(2026, 3, 10)]
    rows = [
        (datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC),),  # exactly midnight -> the 10th
        (datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC),),  # one second earlier -> the 9th
    ]

    counts = _counts_by_day(rows, keys)

    assert counts[date(2026, 3, 10)] == 1
    assert counts[date(2026, 3, 9)] == 1


def test_mean_returns_none_for_empty_sample() -> None:
    """An empty day has no average — ``None``, never a division error or 0.0.

    ``0.0`` would be a lie (a day with no turns did not average zero confidence).
    """
    assert _mean([]) is None


def test_mean_is_rounded() -> None:
    """Averages are rounded to 6dp so JSON output is stable across runs."""
    assert _mean([1, 2]) == 1.5
    assert _mean([1, 1, 2]) == round(4 / 3, 6)
    assert _mean([0]) == 0.0


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


async def test_overview_requires_auth(client) -> None:
    """Analytics leak business data, so the endpoint must reject anonymous calls."""
    response = await client.get("/api/v1/reports/overview")

    assert response.status_code == 401


async def test_overview_on_empty_database(client, auth_headers) -> None:
    """Every metric is zero/None on a fresh DB — no crash, no invented numbers."""
    response = await client.get("/api/v1/reports/overview", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["conversations"]["total"] == 0
    assert body["messages"]["total"] == 0
    assert body["bookings"]["total"] == 0
    assert body["escalations"]["total"] == 0
    assert body["tasks"]["open"] == 0
    # No samples -> the quality metrics are null rather than a fake 0.
    assert body["agent_quality"]["avg_confidence"] is None
    assert body["agent_quality"]["avg_latency_ms"] is None
    assert body["agent_quality"]["total_cost_usd"] == 0.0
    assert body["generated_at"]


async def test_overview_counts_real_rows(client, auth_headers, db_session) -> None:
    """Counters reflect the rows actually present, not just the empty case."""
    conversation = Conversation(
        channel="web",
        customer_ref="Nguyễn Văn A",
        status=ConversationStatus.OPEN,
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)

    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="Tôi muốn đặt vé đi Đà Nẵng",
                confidence=0.9,
                latency_ms=1200,
                cost_usd=0.001,
            ),
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="Tôi có thể giúp bạn.",
                confidence=0.8,
                latency_ms=900,
                cost_usd=0.002,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/reports/overview", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["conversations"]["total"] == 1
    assert body["conversations"]["open"] == 1
    assert body["messages"]["total"] == 2
    assert body["messages"]["last_24h"] == 2
    assert body["messages"]["last_7d"] == 2


async def test_volume_returns_contiguous_zero_filled_series(client, auth_headers) -> None:
    """The time series has exactly ``days`` points, one per day, zero-filled.

    This is the portability contract: the DB engine never does the bucketing, so
    the shape is identical on SQLite and Postgres.
    """
    response = await client.get("/api/v1/reports/volume", params={"days": 7}, headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days"] == 7
    points = body["points"]
    assert len(points) == 7

    dates = [date.fromisoformat(p["date"]) for p in points]
    assert all(b - a == timedelta(days=1) for a, b in zip(dates, dates[1:], strict=False))
    assert dates[0] == date.fromisoformat(body["start"])
    assert dates[-1] == date.fromisoformat(body["end"])

    for point in points:
        assert point["conversations_created"] == 0
        assert point["messages_created"] == 0
        assert point["bookings_created"] == 0
        assert point["escalations_created"] == 0


async def test_volume_rejects_out_of_range_days(client, auth_headers) -> None:
    """``days`` is bounded; an unbounded series would let a caller scan the table."""
    zero = await client.get("/api/v1/reports/volume", params={"days": 0}, headers=auth_headers)
    too_many = await client.get(
        "/api/v1/reports/volume", params={"days": 5000}, headers=auth_headers
    )

    assert zero.status_code == 422
    assert too_many.status_code == 422


async def test_volume_counts_rows_on_the_right_day(client, auth_headers, db_session) -> None:
    """A conversation created today lands on today's point.

    Guards the ``created_at >= start`` filter and the day bucketing together.
    """
    db_session.add(
        Conversation(channel="web", customer_ref="Trần Thị B", status=ConversationStatus.OPEN)
    )
    await db_session.commit()

    response = await client.get("/api/v1/reports/volume", params={"days": 3}, headers=auth_headers)

    assert response.status_code == 200, response.text
    points = response.json()["points"]

    assert points[-1]["conversations_created"] == 1
    assert points[0]["conversations_created"] == 0


async def test_agent_performance_series_shape(client, auth_headers) -> None:
    """Per-day agent quality metrics, one point per day."""
    response = await client.get(
        "/api/v1/reports/agent-performance", params={"days": 5}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days"] == 5
    assert len(body["points"]) == 5

    for point in body["points"]:
        assert point["turns"] == 0
        assert point["tool_calls_total"] == 0
        assert point["tool_calls_failed"] == 0
        # No turns that day -> no rate, rather than a fabricated one.
        assert point["escalation_rate"] == 0.0
        assert point["tool_failure_rate"] == 0.0
        assert point["total_cost_usd"] == 0.0
        assert point["avg_confidence"] is None
        assert point["avg_latency_ms"] is None


async def test_agent_performance_aggregates_assistant_turns(
    client, auth_headers, db_session
) -> None:
    """Only ASSISTANT messages count as agent turns, and their cost is summed.

    A USER message in the same conversation must not inflate the turn count —
    otherwise every quality metric is diluted by the customer's own messages.
    """
    conversation = Conversation(
        channel="web", customer_ref="Lê Văn C", status=ConversationStatus.OPEN
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)

    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="hàng của tôi đang ở đâu?",
            ),
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="Để tôi kiểm tra.",
                confidence=0.6,
                latency_ms=1500,
                cost_usd=0.004,
            ),
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="Đơn đang ở kho Hà Nội.",
                confidence=0.8,
                latency_ms=500,
                cost_usd=0.006,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/reports/agent-performance", params={"days": 3}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    today = response.json()["points"][-1]

    assert today["turns"] == 2  # the USER message is not a turn
    assert today["avg_confidence"] == round((0.6 + 0.8) / 2, 6)
    assert today["avg_latency_ms"] == 1000.0
    assert today["total_cost_usd"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# helper/DB agreement
# ---------------------------------------------------------------------------


async def test_volume_matches_direct_query(client, auth_headers, db_session) -> None:
    """The endpoint's numbers equal a plain ``COUNT(*)`` of the same window.

    This is the test that would have caught the earlier ``MissingGreenlet``-class
    of bug: an ORM/aggregation mismatch rather than a wrong-looking number.
    """
    db_session.add_all(
        [
            Conversation(channel="web", customer_ref="A", status=ConversationStatus.OPEN),
            Conversation(channel="zalo", customer_ref="B", status=ConversationStatus.RESOLVED),
        ]
    )
    await db_session.commit()

    expected = len((await db_session.execute(select(Conversation.id))).all())

    response = await client.get("/api/v1/reports/volume", params={"days": 1}, headers=auth_headers)

    assert response.status_code == 200, response.text
    points = response.json()["points"]

    assert len(points) == 1
    assert points[-1]["conversations_created"] == expected == 2
