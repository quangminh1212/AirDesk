"""OpenSky provider tests.

These run fully offline: the live-fetch method is monkeypatched so the suite
never depends on the OpenSky Network being reachable. They prove the two
guarantees the provider exists for:

1. It is *configured* and available with NO credentials/login.
2. It always returns Vietnamese carriers (VN/VJ/QH/VU/9G) for a VN route, even
   when the live ADS-B snapshot has no Vietnamese traffic.
"""

from __future__ import annotations

import pytest

from app.providers.base_types import CabinClass, FlightSearchQuery, PassengerInfo
from app.providers.opensky import (
    VN_CARRIER_IATA,
    OpenSkyProvider,
    estimate_fare_vnd,
    haversine_km,
)

SAMPLE_STATES = [
    # icao24, callsign, origin_country, t_pos, lon, lat, baro_alt, on_ground,
    # velocity, true_track, vert_rate, ...
    ["abc123", "HVN1234", "Vietnam", 0, 106.6, 10.8, 10000.0, False, 250.0, 45.0, 0.0],
    ["def456", "VJC5678", "Vietnam", 0, 105.8, 21.2, 9500.0, False, 240.0, 180.0, 0.0],
]


@pytest.fixture
def provider() -> OpenSkyProvider:
    return OpenSkyProvider()


def test_provider_requires_no_credentials(provider: OpenSkyProvider) -> None:
    """The whole point: usable with zero login/credentials."""
    assert provider.configured is True
    assert provider.name == "opensky"


def test_haversine_sgn_han() -> None:
    from app.providers.opensky import AIRPORT_COORDS

    dist = haversine_km(AIRPORT_COORDS["SGN"], AIRPORT_COORDS["HAN"])
    # SGN->HAN is roughly 1,140-1,160 km great-circle.
    assert 1050 < dist < 1250


def test_fare_estimate_is_positive_and_carrier_sensitive() -> None:
    base_vn, tax_vn, total_vn = estimate_fare_vnd(1100, "VN")
    base_vj, _, total_vj = estimate_fare_vnd(1100, "VJ")
    assert total_vn > 0 and total_vj > 0
    # Vietnam Airlines is priced above VietJet in our multiplier table.
    assert total_vn > total_vj
    assert total_vn == base_vn + tax_vn


@pytest.mark.asyncio
async def test_search_uses_live_states(provider, monkeypatch) -> None:
    async def fake_states():
        return SAMPLE_STATES

    monkeypatch.setattr(provider, "_live_states", fake_states)
    query = FlightSearchQuery(origin="SGN", destination="HAN", departure_date="2026-05-01")
    offers = await provider.search(query)

    assert offers, "expected offers from live states"
    call_signs = {o.segments[0].flight_number for o in offers}
    assert "HVN1234" in call_signs
    live = [o for o in offers if o.raw.get("data_kind") == "live-adsb"]
    assert live, "expected at least one live-adsb offer"
    assert live[0].fare_type == "live-estimate"
    assert live[0].currency == "VND"


@pytest.mark.asyncio
async def test_search_supplements_vietnamese_carriers(provider, monkeypatch) -> None:
    """With no live VN traffic, the provider still returns all VN carriers."""

    async def no_states():
        return []

    monkeypatch.setattr(provider, "_live_states", no_states)
    query = FlightSearchQuery(origin="SGN", destination="HAN", departure_date="2026-05-01")
    offers = await provider.search(query)

    carriers = {o.primary_carrier for o in offers}
    assert carriers >= VN_CARRIER_IATA, f"missing VN carriers: {VN_CARRIER_IATA - carriers}"
    for o in offers:
        assert o.origin == "SGN" and o.destination == "HAN"


@pytest.mark.asyncio
async def test_search_degrades_gracefully_on_network_error(provider, monkeypatch) -> None:
    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(provider, "_live_states", boom)
    query = FlightSearchQuery(origin="SGN", destination="HAN", departure_date="2026-05-01")
    offers = await provider.search(query)
    # Falls back to scheduled estimates rather than raising.
    assert offers
    assert all(o.raw.get("data_kind") == "scheduled-estimate" for o in offers)


@pytest.mark.asyncio
async def test_price_and_order_most(provider, monkeypatch) -> None:
    async def fake_states():
        return SAMPLE_STATES

    monkeypatch.setattr(provider, "_live_states", fake_states)
    query = FlightSearchQuery(
        origin="SGN", destination="HAN", departure_date="2026-05-01", cabin=CabinClass.BUSINESS
    )
    offers = await provider.search(query)
    priced = await provider.price(offers[0])
    assert priced.total_amount > 0

    pax = [PassengerInfo(passenger_type="adult", first_name="An", last_name="Nguyen")]
    result = await provider.order(offers[0], pax, contact_email="an@example.com")
    assert result.booking_reference.startswith("OSK")
    assert result.status == "simulated"
    assert result.raw.get("simulated") is True
