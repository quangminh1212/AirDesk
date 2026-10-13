"""Mock provider + registry tests (fully offline, deterministic)."""

from __future__ import annotations

import pytest

from app.providers.base_types import FlightSearchQuery, PassengerInfo
from app.providers.mock import MockFlightProvider
from app.providers.registry import (
    available_provider_names,
    get_provider,
    search_flights,
)


def _query() -> FlightSearchQuery:
    return FlightSearchQuery(origin="SGN", destination="HAN", departure_date="2026-05-01")


@pytest.mark.asyncio
async def test_mock_search_is_deterministic() -> None:
    provider = MockFlightProvider()
    first = await provider.search(_query())
    second = await provider.search(_query())
    assert [o.offer_id for o in first] == [o.offer_id for o in second]
    assert first, "mock provider must return offers"


@pytest.mark.asyncio
async def test_mock_covers_vietnamese_carriers() -> None:
    offers = await MockFlightProvider().search(_query())
    carriers = {o.primary_carrier for o in offers}
    assert carriers & {"VN", "VJ", "QH", "VU", "9G"}


@pytest.mark.asyncio
async def test_mock_price_and_order() -> None:
    provider = MockFlightProvider()
    offers = await provider.search(_query())
    priced = await provider.price(offers[0])
    assert priced.total_amount >= 0

    pax = [PassengerInfo(passenger_type="adult", first_name="Binh", last_name="Tran")]
    result = await provider.order(offers[0], pax, contact_email="binh@example.com")
    assert result.booking_reference
    assert result.provider == "mock"


def test_opensky_and_mock_available_without_credentials() -> None:
    """Available providers must include the keyless ones."""
    names = available_provider_names()
    assert "mock" in names
    assert "opensky" in names


def test_get_provider_rejects_unknown() -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        get_provider("does-not-exist")


@pytest.mark.asyncio
async def test_registry_search_merges_and_sorts() -> None:
    offers = await search_flights(_query(), provider_name="mock")
    assert offers
    amounts = [o.total_amount for o in offers]
    assert amounts == sorted(amounts), "search results must be cheapest-first"
