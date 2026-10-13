"""Flight search, pricing and offer-retrieval endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUserDep
from app.core.errors import NotFoundError, ValidationError
from app.providers.base_types import FlightSearchQuery
from app.providers.codec import offer_from_dict, offer_to_dict
from app.providers.registry import available_provider_names, get_provider, search_flights
from app.schemas.flight import (
    FlightOfferSchema,
    FlightSearchRequest,
    SegmentSchema,
)
from app.services.offer_cache import cache_offer, get_cached_offer

router = APIRouter(prefix="/flights", tags=["flights"])


def to_offer_schema(offer) -> FlightOfferSchema:  # type: ignore[no-untyped-def]
    return FlightOfferSchema(
        offer_id=offer.offer_id,
        provider=offer.provider,
        origin=offer.origin,
        destination=offer.destination,
        departure_at=offer.departure_at,
        return_at=offer.return_at,
        airline_code=offer.primary_carrier,
        airline_name=offer.primary_carrier_name,
        segments=[
            SegmentSchema(
                origin=s.origin,
                destination=s.destination,
                departure_at=s.departure_at,
                arrival_at=s.arrival_at,
                carrier_code=s.carrier_code,
                carrier_name=s.carrier_name,
                flight_number=s.flight_number,
                duration_minutes=s.duration_minutes,
                aircraft=s.aircraft,
                cabin=s.cabin,
                fare_basis=s.fare_basis,
                baggage_allowance=s.baggage_allowance,
            )
            for s in offer.segments
        ],
        total_amount=offer.total_amount,
        base_amount=offer.base_amount,
        taxes_amount=offer.taxes_amount,
        currency=offer.currency,
        stops=offer.stops,
        total_duration_minutes=offer.total_duration_minutes,
        seats_available=offer.seats_available,
        fare_type=offer.fare_type,
        refundable=offer.refundable,
        baggage_allowance=offer.baggage_allowance,
        expires_at=offer.expires_at,
        kind=offer.kind.value,
        price_guarantee_until=offer.price_guarantee_until,
        is_bookable=offer.is_bookable,
        markup_amount=offer.markup_amount,
        airline_amount=offer.airline_amount,
    )


@router.get("/providers", summary="List configured flight providers")
async def list_providers(_user: CurrentUserDep) -> dict[str, list[str]]:
    return {"providers": available_provider_names()}


@router.post(
    "/search",
    response_model=list[FlightOfferSchema],
    summary="Search flights across configured providers",
)
async def search(_user: CurrentUserDep, payload: FlightSearchRequest) -> list[FlightOfferSchema]:
    query = FlightSearchQuery(
        origin=payload.origin,
        destination=payload.destination,
        departure_date=payload.departure_date.isoformat(),
        return_date=payload.return_date.isoformat() if payload.return_date else None,
        adults=payload.adults,
        children=payload.children,
        infants=payload.infants,
        cabin=payload.cabin,
        currency=payload.currency,
        max_results=payload.max_results,
        non_stop=payload.non_stop,
    )
    offers = await search_flights(
        query, provider_name=None if payload.provider == "auto" else payload.provider
    )
    # Cache each offer so it can be re-priced and ordered later by id.
    for offer in offers:
        await cache_offer(offer.provider, offer.offer_id, offer_to_dict(offer), kind="shopped")
    return [to_offer_schema(o) for o in offers]


@router.get(
    "/offers/{provider}/{offer_id}",
    response_model=FlightOfferSchema,
    summary="Fetch a previously cached offer",
)
async def get_offer(_user: CurrentUserDep, provider: str, offer_id: str) -> FlightOfferSchema:
    cached = await get_cached_offer(provider, offer_id)
    if cached is None:
        raise NotFoundError("Offer not found or expired. Please search again.")
    return to_offer_schema(offer_from_dict(cached))


@router.post(
    "/offers/{provider}/{offer_id}/price",
    response_model=FlightOfferSchema,
    summary="Re-price a cached offer (confirm availability & taxes)",
)
async def price_offer(_user: CurrentUserDep, provider: str, offer_id: str) -> FlightOfferSchema:
    cached = await get_cached_offer(provider, offer_id)
    if cached is None:
        raise NotFoundError("Offer not found or expired. Please search again.")
    offer = offer_from_dict(cached)
    try:
        priced = await get_provider(provider).price(offer)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(
            "Could not re-price offer; it may have expired.",
            details={"error": str(exc)},
        ) from exc
    # Store as a *priced* offer and remember the shopped offer it came from,
    # so the order step can enforce the NDC rule (only priced offers bookable).
    from app.services.offer_cache import cache_priced_offer

    await cache_priced_offer(provider, priced.offer_id, offer_id, offer_to_dict(priced))
    return to_offer_schema(priced)
