"""Serialise/deserialise provider-agnostic ``FlightOffer`` objects.

Offers must survive a round-trip through the cache (and the client), because
pricing and ordering happen in later requests. This module is the single
source of truth for that encoding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.providers.base_types import FlightOffer, OfferKind, Segment


def offer_to_dict(offer: FlightOffer) -> dict[str, Any]:
    """Encode a FlightOffer into a JSON-safe dict."""
    return {
        "provider": offer.provider,
        "offer_id": offer.offer_id,
        "segments": [
            {
                "origin": s.origin,
                "destination": s.destination,
                "departure_at": s.departure_at.isoformat(),
                "arrival_at": s.arrival_at.isoformat(),
                "carrier_code": s.carrier_code,
                "carrier_name": s.carrier_name,
                "flight_number": s.flight_number,
                "duration_minutes": s.duration_minutes,
                "aircraft": s.aircraft,
                "cabin": s.cabin,
                "fare_basis": s.fare_basis,
                "baggage_allowance": s.baggage_allowance,
            }
            for s in offer.segments
        ],
        "total_amount": offer.total_amount,
        "base_amount": offer.base_amount,
        "taxes_amount": offer.taxes_amount,
        "currency": offer.currency,
        "seats_available": offer.seats_available,
        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
        "fare_type": offer.fare_type,
        "refundable": offer.refundable,
        "baggage_allowance": offer.baggage_allowance,
        # NDC-style offer metadata (must survive the cache round-trip).
        "kind": offer.kind.value,
        "shopped_offer_id": offer.shopped_offer_id,
        "price_guarantee_until": (
            offer.price_guarantee_until.isoformat() if offer.price_guarantee_until else None
        ),
        "markup_amount": offer.markup_amount,
        "airline_amount": offer.airline_amount,
        "raw": offer.raw,
    }


def offer_from_dict(data: dict[str, Any]) -> FlightOffer:
    """Decode a dict (from cache) back into a FlightOffer."""
    segments = [
        Segment(
            origin=s["origin"],
            destination=s["destination"],
            departure_at=datetime.fromisoformat(s["departure_at"]),
            arrival_at=datetime.fromisoformat(s["arrival_at"]),
            carrier_code=s["carrier_code"],
            carrier_name=s["carrier_name"],
            flight_number=s["flight_number"],
            duration_minutes=s.get("duration_minutes", 0),
            aircraft=s.get("aircraft"),
            cabin=s.get("cabin", "economy"),
            fare_basis=s.get("fare_basis"),
            baggage_allowance=s.get("baggage_allowance"),
        )
        for s in data.get("segments", [])
    ]
    return FlightOffer(
        provider=data["provider"],
        offer_id=data["offer_id"],
        segments=segments,
        total_amount=data.get("total_amount", 0),
        base_amount=data.get("base_amount", 0),
        taxes_amount=data.get("taxes_amount", 0),
        currency=data.get("currency", "VND"),
        seats_available=data.get("seats_available"),
        expires_at=(datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None),
        fare_type=data.get("fare_type"),
        refundable=data.get("refundable"),
        baggage_allowance=data.get("baggage_allowance"),
        kind=OfferKind(data.get("kind", OfferKind.SHOPPED.value)),
        shopped_offer_id=data.get("shopped_offer_id"),
        price_guarantee_until=(
            datetime.fromisoformat(data["price_guarantee_until"])
            if data.get("price_guarantee_until")
            else None
        ),
        markup_amount=data.get("markup_amount", 0),
        airline_amount=data.get("airline_amount", 0),
        raw=data.get("raw", {}),
    )
