"""Amadeus order-confirmation path (fully offline).

The ``order`` call is what turns a priced offer into issued tickets, so the
money and ticket extraction here sit directly on the "did the customer actually
get ticketed?" boundary. These tests pin that behaviour against a realistic
``/v1/booking/flight-orders`` response body, with the HTTP layer stubbed out.

Regression guard: the total used to be read via a non-existent ``_to_minor``
helper, which raised ``AttributeError`` on every real order. Everything here
fails loudly if that path is broken again.
"""

from __future__ import annotations

import pytest

from app.providers.amadeus import AmadeusProvider
from app.providers.base_types import PassengerInfo

# A trimmed but structurally faithful flight-order response: the priced offer is
# echoed back under ``flightOffers`` and the issued documents live on the
# traveller, mirroring Amadeus's documented order schema.
_ORDER_RESPONSE = {
    "data": {
        "type": "flight-order",
        "id": "eJzT1234abcd",
        "queuingOfficeId": "NCE1A0980",
        "associatedRecords": [{"reference": "ABC123", "originSystemCode": "GDS"}],
        "flightOffers": [
            {
                "type": "flight-offer",
                "id": "1",
                "price": {
                    "currency": "VND",
                    "total": "4820000",
                    "base": "4000000",
                    "grandTotal": "4820000",
                },
            }
        ],
        "travelers": [
            {
                "id": "1",
                "name": {"firstName": "NGUYEN", "lastName": "AN"},
                "documents": [
                    {
                        "documentType": "TICKET",
                        "number": "738-1234567890",
                        "status": "ISSUED",
                    }
                ],
            }
        ],
    }
}


def _provider() -> AmadeusProvider:
    """The provider reads its credentials from settings; no network on init."""
    return AmadeusProvider()


def _offer(provider: AmadeusProvider):
    """Build one offer via the provider's own parser, so the round-trip is real."""
    raw = {
        "type": "flight-offer",
        "id": "1",
        "price": {"currency": "VND", "total": "4820000", "grandTotal": "4820000"},
        "itineraries": [
            {
                "duration": "PT2H5M",
                "segments": [
                    {
                        "departure": {"iataCode": "SGN", "at": "2026-05-01T08:00:00"},
                        "arrival": {"iataCode": "HAN", "at": "2026-05-01T10:05:00"},
                        "carrierCode": "VN",
                        "number": "123",
                    }
                ],
            }
        ],
    }
    return provider._to_offer(raw)


async def _order_with(monkeypatch: pytest.MonkeyPatch, payload: dict):
    provider = _provider()

    async def fake_request(method: str, url: str, **kwargs: object) -> dict:
        assert method == "POST"
        assert url == "/v1/booking/flight-orders"
        return payload

    async def fake_auth_headers() -> dict[str, str]:
        return {"Authorization": "Bearer test"}

    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "_auth_headers", fake_auth_headers)

    return await provider.order(
        _offer(provider),
        [PassengerInfo(passenger_type="ADULT", first_name="NGUYEN", last_name="AN")],
        contact_email="khach@example.com",
    )


@pytest.mark.asyncio
async def test_order_reads_grand_total(monkeypatch: pytest.MonkeyPatch) -> None:
    """The echoed offer's grandTotal becomes the order total (integer đồng)."""
    result = await _order_with(monkeypatch, _ORDER_RESPONSE)
    assert result.total_amount == 4820000
    assert result.currency == "VND"


@pytest.mark.asyncio
async def test_order_falls_back_to_total_when_grand_total_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some sandbox payloads omit grandTotal; total must still be honoured."""
    payload = {
        "data": {
            "id": "eJzT1234abcd",
            "flightOffers": [{"price": {"currency": "VND", "total": "1500000"}}],
            "travelers": [],
        }
    }
    result = await _order_with(monkeypatch, payload)
    assert result.total_amount == 1500000


@pytest.mark.asyncio
async def test_order_survives_missing_price_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """A price-less response yields 0, never an exception."""
    payload = {"data": {"id": "x", "flightOffers": [], "travelers": []}}
    result = await _order_with(monkeypatch, payload)
    assert result.total_amount == 0


@pytest.mark.asyncio
async def test_order_extracts_issued_tickets(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await _order_with(monkeypatch, _ORDER_RESPONSE)
    assert result.tickets, "the issued ticket must surface as a ticket record"
    assert result.tickets[0]["number"] == "738-1234567890"
    assert result.tickets[0]["status"] == "ISSUED"
    assert result.tickets[0]["passenger"] == "NGUYEN AN"


@pytest.mark.asyncio
async def test_order_carries_booking_reference_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _order_with(monkeypatch, _ORDER_RESPONSE)
    assert result.booking_reference == "ABC123"
    assert result.status == "confirmed"
    assert result.provider == "amadeus"


@pytest.mark.asyncio
async def test_order_with_a_note_only_ticket_is_not_a_document_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document with no number must not masquerade as a real ticket.

    ``_apply_tickets`` counts only tickets carrying a document number before
    promoting a booking to TICKETED, so extraction must not invent one.
    """
    payload = {
        "data": {
            "id": "1",
            "flightOffers": [{"price": {"grandTotal": "1000"}}],
            "travelers": [
                {
                    "name": {"firstName": "A", "lastName": "B"},
                    "documents": [{"documentType": "TICKET", "status": "SIMULATED"}],
                }
            ],
        }
    }
    result = await _order_with(monkeypatch, payload)
    assert result.tickets[0]["number"] is None
