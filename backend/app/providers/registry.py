"""Provider registry: resolve configured providers and aggregate search."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.providers.amadeus import AmadeusProvider
from app.providers.base import FlightProvider
from app.providers.base_types import FlightOffer, FlightSearchQuery, OrderResult, PassengerInfo
from app.providers.duffel import DuffelProvider
from app.providers.mock import MockFlightProvider
from app.providers.opensky import OpenSkyProvider

logger = get_logger(__name__)

_REGISTRY: dict[str, type[FlightProvider]] = {
    "amadeus": AmadeusProvider,
    "duffel": DuffelProvider,
    "opensky": OpenSkyProvider,
    "mock": MockFlightProvider,
}


def available_provider_names() -> list[str]:
    """Names of providers that are configured and usable right now."""
    return [
        name
        for name in get_settings().flight_providers_list
        if name in _REGISTRY and _REGISTRY[name]().configured
    ]


def get_provider(name: str) -> FlightProvider:
    """Instantiate a provider by name, validating it is configured."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValidationError(
            f"Unknown flight provider '{name}'.", details={"known": list(_REGISTRY)}
        )
    provider = cls()
    if not provider.configured:
        raise ValidationError(f"Provider '{name}' is not configured (missing credentials).")
    return provider


def _providers_for(provider_name: str | None) -> list[FlightProvider]:
    if provider_name and provider_name != "auto":
        return [get_provider(provider_name)]
    names = available_provider_names()
    if not names:
        # Always guarantee at least the mock provider for local/dev resilience.
        names = ["mock"]
    return [get_provider(n) for n in names]


async def search_flights(
    query: FlightSearchQuery, *, provider_name: str | None = None
) -> list[FlightOffer]:
    """Search one or all providers in parallel and merge, cheapest first."""
    providers = _providers_for(provider_name)
    results = await asyncio.gather(*(p.search(query) for p in providers), return_exceptions=True)

    merged: list[FlightOffer] = []
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("provider_search_failed", provider=provider.name, error=str(result))
            continue
        merged.extend(result)

    merged.sort(key=lambda o: o.total_amount)
    return merged[: query.max_results]


async def price_offer(offer: FlightOffer) -> FlightOffer:
    """Re-price an offer via its originating provider."""
    return await get_provider(offer.provider).price(offer)


async def create_order(
    offer: FlightOffer,
    passengers: list[PassengerInfo],
    *,
    contact_email: str,
    contact_phone: str | None = None,
) -> OrderResult:
    """Create a booking order via the offer's provider."""
    return await get_provider(offer.provider).order(
        offer, passengers, contact_email=contact_email, contact_phone=contact_phone
    )
