"""Logistics / cargo tests: service state machine, guardrails, and API surface.

Two layers are covered deliberately:

* ``ShipmentService`` unit tests pin the state machine and the guardrails that
  route work to a human (high declared value, dangerous goods, statuses that
  need attention). These are the rules an operator depends on, so they are
  asserted directly rather than only through HTTP.
* API tests pin the contract the frontend and the agent tools consume, including
  that ``allowed_next`` is exposed so a caller never has to re-implement the
  transition table.
"""

from __future__ import annotations

import pytest

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.shipment import Shipment, ShipmentMode, ShipmentPriority, ShipmentStatus
from app.services.shipment import ShipmentService, default_eta


async def _reload(db_session, shipment: Shipment) -> Shipment:
    """Re-fetch with events eager-loaded.

    ``shipment.events`` is a lazy relationship: touching it after a ``commit()``
    in async code raises ``MissingGreenlet`` because SQLAlchemy cannot emit IO
    from a sync attribute access. Every assertion on the timeline goes through
    this helper so the tests exercise production behaviour, not lazy loading.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db_session.execute(
        select(Shipment).where(Shipment.id == shipment.id).options(selectinload(Shipment.events))
    )
    return result.scalar_one()


async def _make(db_session, **overrides):
    service = ShipmentService(db_session)
    kwargs = {
        "origin": "SGN",
        "destination": "HAN",
        "mode": ShipmentMode.AIR,
        "priority": ShipmentPriority.NORMAL,
        "description": "Hộp tài liệu",
        "pieces": 1,
        "weight_kg": 2.5,
    }
    kwargs.update(overrides)
    shipment = await service.create_shipment(**kwargs)
    await db_session.commit()
    shipment = await _reload(db_session, shipment)
    return service, shipment


# --- service: creation -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_generates_reference_and_created_event(db_session) -> None:
    service, shipment = await _make(db_session)

    assert shipment.reference.startswith("SHP-")
    assert len(shipment.reference) == 10  # "SHP-" + 6 chars
    assert shipment.status is ShipmentStatus.DRAFT
    assert [e.event_type for e in shipment.events] == ["created"]


@pytest.mark.asyncio
async def test_create_rejects_missing_route_and_zero_pieces(db_session) -> None:
    service = ShipmentService(db_session)

    with pytest.raises(ValidationError):
        await service.create_shipment(origin="", destination="HAN")

    with pytest.raises(ValidationError):
        await service.create_shipment(origin="SGN", destination="HAN", pieces=0)


@pytest.mark.asyncio
async def test_create_is_idempotent_on_key(db_session) -> None:
    """A retried POST must not create a second consignment."""
    service = ShipmentService(db_session)
    first = await service.create_shipment(
        origin="SGN", destination="HAN", idempotency_key="key-abc"
    )
    second = await service.create_shipment(
        origin="SGN", destination="HAN", idempotency_key="key-abc"
    )

    assert first.id == second.id


@pytest.mark.asyncio
async def test_references_are_unique_across_many_creates(db_session) -> None:
    service = ShipmentService(db_session)
    refs = set()
    for _ in range(25):
        s = await service.create_shipment(origin="SGN", destination="HAN")
        refs.add(s.reference)
    assert len(refs) == 25


# --- service: guardrails ---------------------------------------------------


@pytest.mark.asyncio
async def test_high_declared_value_opens_approval_task(db_session) -> None:
    """Over the ceiling -> a human approves. This is the core guardrail."""
    _, shipment = await _make(db_session, declared_value=900_000_000)

    assert shipment.status is ShipmentStatus.DRAFT  # never auto-advances
    tasks = await ShipmentService(db_session).open_tasks_for(shipment)
    assert len(tasks) == 1
    assert tasks[0].task_type.value == "logistics"
    assert "Duyệt" in tasks[0].title


@pytest.mark.asyncio
async def test_value_under_ceiling_needs_no_approval(db_session) -> None:
    _, shipment = await _make(db_session, declared_value=1_000_000)
    assert await ShipmentService(db_session).open_tasks_for(shipment) == []


@pytest.mark.asyncio
async def test_dangerous_goods_always_needs_approval(db_session) -> None:
    """Dangerous goods must escalate even when the value is low."""
    _, shipment = await _make(db_session, declared_value=0, is_dangerous_goods=True)

    tasks = await ShipmentService(db_session).open_tasks_for(shipment)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_attention_status_opens_operator_task(db_session) -> None:
    service, shipment = await _make(db_session, declared_value=0)
    await service.update_status(shipment, ShipmentStatus.BOOKED)
    await service.update_status(shipment, ShipmentStatus.PICKED_UP)
    await service.update_status(shipment, ShipmentStatus.IN_TRANSIT)
    await service.update_status(shipment, ShipmentStatus.DELAYED, note="Chậm chuyến")
    await db_session.commit()

    tasks = await service.open_tasks_for(shipment)
    assert len(tasks) == 1
    assert "Xử lý" in tasks[0].title
    assert "delayed" in tasks[0].title


@pytest.mark.asyncio
async def test_normal_transition_opens_no_task(db_session) -> None:
    service, shipment = await _make(db_session, declared_value=0)
    await service.update_status(shipment, ShipmentStatus.BOOKED)
    await db_session.commit()
    assert await service.open_tasks_for(shipment) == []


# --- service: state machine ------------------------------------------------


@pytest.mark.asyncio
async def test_illegal_transition_is_rejected(db_session) -> None:
    service, shipment = await _make(db_session)
    with pytest.raises(ValidationError):
        await service.update_status(shipment, ShipmentStatus.DELIVERED)
    assert shipment.status is ShipmentStatus.DRAFT  # unchanged on failure


@pytest.mark.asyncio
async def test_full_happy_path_to_delivered(db_session) -> None:
    service, shipment = await _make(db_session, declared_value=0)
    for status in (
        ShipmentStatus.BOOKED,
        ShipmentStatus.PICKED_UP,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.OUT_FOR_DELIVERY,
        ShipmentStatus.DELIVERED,
    ):
        await service.update_status(shipment, status)
    await db_session.commit()

    assert shipment.status is ShipmentStatus.DELIVERED
    assert shipment.delivered_at is not None


@pytest.mark.asyncio
async def test_pickup_stamps_picked_up_at(db_session) -> None:
    service, shipment = await _make(db_session)
    await service.update_status(shipment, ShipmentStatus.BOOKED)
    await service.update_status(shipment, ShipmentStatus.PICKED_UP)
    assert shipment.picked_up_at is not None


@pytest.mark.asyncio
async def test_same_status_is_a_noop(db_session) -> None:
    """Re-reporting the current status must not raise or duplicate events."""
    service, shipment = await _make(db_session)
    before = len(shipment.events)
    await service.update_status(shipment, ShipmentStatus.DRAFT)
    await db_session.commit()
    shipment = await _reload(db_session, shipment)
    assert len(shipment.events) == before


@pytest.mark.asyncio
async def test_expected_next_statuses_matches_table(db_session) -> None:
    service, shipment = await _make(db_session)
    assert set(service.expected_next_statuses(shipment)) == {"booked", "cancelled"}


@pytest.mark.asyncio
async def test_cancel_terminal_then_rejects(db_session) -> None:
    service, shipment = await _make(db_session)
    await service.cancel(shipment, reason="Khách huỷ")
    await db_session.commit()
    assert shipment.status is ShipmentStatus.CANCELLED

    with pytest.raises(ConflictError):
        await service.cancel(shipment, reason="again")


# --- service: tracking -----------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_tracking_resolves_all_identifiers(db_session) -> None:
    service, shipment = await _make(db_session)
    shipment.tracking_number = "123-45678901"
    shipment.waybill_number = "WB-999"
    await db_session.commit()

    assert (await service.find_by_tracking(shipment.reference)).id == shipment.id
    assert (await service.find_by_tracking("123-45678901")).id == shipment.id
    assert (await service.find_by_tracking("WB-999")).id == shipment.id
    assert await service.find_by_tracking("NOPE") is None


@pytest.mark.asyncio
async def test_get_raises_for_unknown_id(db_session) -> None:
    with pytest.raises(NotFoundError):
        await ShipmentService(db_session).get("does-not-exist")


@pytest.mark.asyncio
async def test_add_tracking_event_appends_to_timeline(db_session) -> None:
    service, shipment = await _make(db_session)
    await service.update_status(shipment, ShipmentStatus.BOOKED)
    await service.update_status(shipment, ShipmentStatus.PICKED_UP)
    await service.add_tracking_event(shipment, event_type="scan", location="SGN")
    await db_session.commit()
    shipment = await _reload(db_session, shipment)

    types = [e.event_type for e in shipment.events]
    assert types[0] == "created"
    assert "scan" in types


# --- service: SLA ----------------------------------------------------------


@pytest.mark.asyncio
async def test_sla_breach_only_when_open_and_past_eta(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    service, shipment = await _make(db_session)
    shipment.status = ShipmentStatus.IN_TRANSIT
    shipment.eta_at = datetime.now(UTC) - timedelta(hours=5)
    assert service.sla_breaches(shipment) is True

    shipment.eta_at = datetime.now(UTC) + timedelta(hours=5)
    assert service.sla_breaches(shipment) is False

    shipment.eta_at = datetime.now(UTC) - timedelta(hours=5)
    shipment.status = ShipmentStatus.DELIVERED
    assert service.sla_breaches(shipment) is False


def test_default_eta_varies_by_mode() -> None:
    air = default_eta(ShipmentMode.AIR)
    sea = default_eta(ShipmentMode.SEA)
    assert sea > air


# --- API -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shipments_require_auth(client) -> None:
    assert (await client.get("/api/v1/shipments")).status_code == 401


@pytest.mark.asyncio
async def test_create_then_get_via_api(client, auth_headers) -> None:
    resp = await client.post(
        "/api/v1/shipments",
        headers=auth_headers,
        json={"origin": "SGN", "destination": "HAN", "pieces": 2, "weight_kg": 3.0},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reference"].startswith("SHP-")
    assert body["status"] == "draft"
    assert body["allowed_next"] == ["booked", "cancelled"]

    got = await client.get(f"/api/v1/shipments/{body['id']}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_api_rejects_invalid_body(client, auth_headers) -> None:
    resp = await client.post(
        "/api/v1/shipments",
        headers=auth_headers,
        json={"origin": "", "destination": ""},
    )
    assert resp.status_code in {400, 422}, resp.text


@pytest.mark.asyncio
async def test_status_endpoint_moves_and_exposes_next(client, auth_headers) -> None:
    created = (
        await client.post(
            "/api/v1/shipments",
            headers=auth_headers,
            json={"origin": "SGN", "destination": "HAN"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/shipments/{created['id']}/status",
        headers=auth_headers,
        json={"status": "booked", "location": "SGN"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "booked"

    bad = await client.post(
        f"/api/v1/shipments/{created['id']}/status",
        headers=auth_headers,
        json={"status": "delivered"},
    )
    assert bad.status_code in {400, 409, 422}, bad.text


@pytest.mark.asyncio
async def test_track_endpoint(client, auth_headers) -> None:
    created = (
        await client.post(
            "/api/v1/shipments",
            headers=auth_headers,
            json={"origin": "SGN", "destination": "DAD"},
        )
    ).json()

    resp = await client.get(f"/api/v1/shipments/track/{created['reference']}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == created["id"]

    missing = await client.get("/api/v1/shipments/track/SHP-NOPE00", headers=auth_headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_scan_before_pickup_is_rejected(client, auth_headers) -> None:
    created = (
        await client.post(
            "/api/v1/shipments",
            headers=auth_headers,
            json={"origin": "SGN", "destination": "HAN"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/shipments/{created['id']}/events",
        headers=auth_headers,
        json={"event_type": "scan", "location": "SGN"},
    )
    assert resp.status_code in {400, 409, 422}, resp.text


@pytest.mark.asyncio
async def test_list_filters_and_stats(client, auth_headers) -> None:
    for dest in ("HAN", "DAD"):
        await client.post(
            "/api/v1/shipments",
            headers=auth_headers,
            json={"origin": "SGN", "destination": dest},
        )

    listed = await client.get("/api/v1/shipments", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    filtered = await client.get("/api/v1/shipments?status=draft", headers=auth_headers)
    assert filtered.json()["total"] == 2

    empty = await client.get("/api/v1/shipments?status=delivered", headers=auth_headers)
    assert empty.json()["total"] == 0

    stats = await client.get("/api/v1/shipments/stats", headers=auth_headers)
    assert stats.status_code == 200
    body = stats.json()
    assert body["total"] == 2
    # ``by_status`` is a list of {status, count} rows; every status bucket is
    # present so the dashboard can render without null checks.
    by_status = {row["status"]: row["count"] for row in body["by_status"]}
    assert set(by_status) >= {"draft", "booked", "delivered"}
    assert by_status["draft"] == 2


@pytest.mark.asyncio
async def test_cancel_endpoint(client, auth_headers) -> None:
    created = (
        await client.post(
            "/api/v1/shipments",
            headers=auth_headers,
            json={"origin": "SGN", "destination": "HAN"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/shipments/{created['id']}/cancel",
        headers=auth_headers,
        json={"reason": "Khách huỷ đơn"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


# --- agent tools -----------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_tools_are_declared_and_implemented() -> None:
    from app.agents.tools import TOOL_SPECS, ToolRuntime

    declared = {s["function"]["name"] for s in TOOL_SPECS}
    assert {"track_shipment", "create_shipment", "update_shipment_status"} <= declared

    implemented = {a[len("_tool_") :] for a in dir(ToolRuntime) if a.startswith("_tool_")}
    # No drift in either direction.
    assert declared == implemented


@pytest.mark.asyncio
async def test_agent_can_create_and_track_shipment(db_session) -> None:
    from app.agents.tools import ToolRuntime

    runtime = ToolRuntime(db_session, conversation_id=None, actor_id=None)

    created = await runtime.run(
        "create_shipment",
        {"origin": "SGN", "destination": "HAN", "mode": "AIR", "pieces": 1},
    )
    assert created.success, created.error
    ref = created.result["reference"]

    tracked = await runtime.run("track_shipment", {"tracking_number": ref})
    assert tracked.success, tracked.error
    assert tracked.result["status"] == "draft"


@pytest.mark.asyncio
async def test_agent_tool_accepts_mixed_case_status(db_session) -> None:
    """LLMs are inconsistent about casing; the tool must normalise it."""
    from app.agents.tools import ToolRuntime

    runtime = ToolRuntime(db_session, conversation_id=None, actor_id=None)
    created = await runtime.run(
        "create_shipment",
        {"origin": "SGN", "destination": "HAN", "mode": "sea"},
    )
    ref = created.result["reference"]
    assert created.result["mode"] == "sea"

    moved = await runtime.run(
        "update_shipment_status", {"tracking_number": ref, "status": "BOOKED"}
    )
    assert moved.success, moved.error
    assert moved.result["status"] == "booked"


@pytest.mark.asyncio
async def test_agent_tool_reports_missing_shipment_as_failure(db_session) -> None:
    from app.agents.tools import ToolRuntime

    runtime = ToolRuntime(db_session, conversation_id=None, actor_id=None)
    out = await runtime.run("track_shipment", {"tracking_number": "SHP-NOPE00"})

    assert out.success is False
    assert "No shipment matches" in (out.error or "")
