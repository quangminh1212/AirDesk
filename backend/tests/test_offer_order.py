"""Tests for the NDC offer->order flow copied from Amadeus/Duffel/Norba.

Covers the rules that make the flow safe:

* a *shopped* offer cannot be booked — it must be priced first (NDC OfferPrice);
* pricing mints a new bookable id, keeps the shopped link, and sets a guarantee;
* ordering a priced offer succeeds and records an ``order_events`` timeline;
* a retried POST with the same ``Idempotency-Key`` returns the same booking.
"""

from __future__ import annotations

import pytest

PAX = [
    {
        "passenger_type": "adult",
        "first_name": "AN",
        "last_name": "NGUYEN",
        "date_of_birth": "1990-01-01",
    }
]


async def _search(client, headers, *, provider="mock"):
    resp = await client.post(
        "/api/v1/flights/search",
        headers=headers,
        json={
            "origin": "SGN",
            "destination": "HAN",
            "departure_date": "2026-05-01",
            "adults": 1,
            "provider": provider,
            "max_results": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    offers = resp.json()
    assert offers
    return offers[0]


def _booking_body(offer, *, provider):
    return {
        "provider": provider,
        "offer_id": offer["offer_id"],
        "cabin": "economy",
        "contact_email": "khach@example.com",
        "contact_phone": "+84900000000",
        "passengers": PAX,
    }


@pytest.mark.asyncio
async def test_search_offers_are_shopped_and_not_bookable(client, auth_headers) -> None:
    offer = await _search(client, auth_headers)
    assert offer["kind"] == "shopped"
    assert offer["is_bookable"] is False


@pytest.mark.asyncio
async def test_shopped_offer_is_refused_at_order(client, auth_headers) -> None:
    """NDC rule: only a priced offer may be ordered."""
    offer = await _search(client, auth_headers)
    resp = await client.post(
        "/api/v1/bookings",
        headers=auth_headers,
        json=_booking_body(offer, provider="mock"),
    )
    assert resp.status_code == 422, resp.text
    assert "priced" in resp.text.lower() or "bookable" in resp.text.lower()


@pytest.mark.asyncio
async def test_price_then_order_full_flow(client, auth_headers) -> None:
    shopped = await _search(client, auth_headers)

    priced_resp = await client.post(
        f"/api/v1/flights/offers/mock/{shopped['offer_id']}/price",
        headers=auth_headers,
    )
    assert priced_resp.status_code == 200, priced_resp.text
    priced = priced_resp.json()
    assert priced["kind"] == "priced"
    assert priced["is_bookable"] is True
    assert priced["price_guarantee_until"] is not None
    # A priced offer gets a new id and keeps the shopped one as the link.
    assert priced["offer_id"] != shopped["offer_id"]
    assert priced["offer_id"] == f"{shopped['offer_id']}_P"

    order_resp = await client.post(
        "/api/v1/bookings",
        headers=auth_headers,
        json=_booking_body(priced, provider="mock"),
    )
    assert order_resp.status_code == 201, order_resp.text
    booking = order_resp.json()
    assert booking["pnr"]
    # The mock provider issues a real document number per passenger, so the
    # order must be promoted to ``ticketed`` — not merely confirmed. Anything
    # weaker means we accepted money without holding a ticket, which is the
    # single most dangerous state for a ticketing agency.
    assert booking["status"] == "ticketed", booking["status"]
    assert booking["payment_status"] == "paid"
    # The order carries an append-only timeline.
    assert booking["events"], "expected an order_events timeline"
    events = {e["event_type"] for e in booking["events"]}
    assert "created" in events

    # Every passenger got a document, and each one is a real ticket number.
    assert booking["tickets"], "expected issued tickets on a ticketed booking"
    assert len(booking["tickets"]) == len(booking["passengers"])
    for ticket in booking["tickets"]:
        assert ticket["ticket_number"], "a ticketed booking needs document numbers"
        assert ticket["status"] == "issued"
        assert ticket["issued_at"] is not None
        assert ticket["pnr"] == booking["pnr"]
        assert ticket["fare_amount"] >= 0 and ticket["tax_amount"] >= 0

    # Per-ticket money must reconcile exactly with the order total (VND has no
    # minor unit, so this is an integer identity, not an approximation).
    assert sum(t["fare_amount"] for t in booking["tickets"]) == booking["base_amount"]
    assert sum(t["tax_amount"] for t in booking["tickets"]) == booking["taxes_amount"]


@pytest.mark.asyncio
async def test_order_is_idempotent(client, auth_headers) -> None:
    """A retried POST with the same Idempotency-Key never double-books."""
    shopped = await _search(client, auth_headers)
    priced = (
        await client.post(
            f"/api/v1/flights/offers/mock/{shopped['offer_id']}/price",
            headers=auth_headers,
        )
    ).json()

    headers = {**auth_headers, "Idempotency-Key": "test-key-123"}
    body = _booking_body(priced, provider="mock")

    first = await client.post("/api/v1/bookings", headers=headers, json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/bookings", headers=headers, json=body)
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["reference"] == second.json()["reference"]
