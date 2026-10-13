"""Amadeus Self-Service flight provider.

Implements the canonical Amadeus flow:
  OAuth2 client_credentials -> Flight Offers Search (v2) ->
  Flight Offers Price -> Flight Create Orders (v2).

The Amadeus test environment returns real schedules for Vietnamese carriers
(VN Vietnam Airlines, VJ VietJet, QH Bamboo, VU Vietravel) and supports the
full price/order cycle with test data. Endpoint and credentials are
configurable so the same adapter targets production.
"""

from __future__ import annotations

import time
from datetime import datetime

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base import HttpFlightProvider
from app.providers.base_types import (
    CabinClass,
    FlightOffer,
    FlightSearchQuery,
    OrderResult,
    PassengerInfo,
    Segment,
    mark_priced,
)

logger = get_logger(__name__)

# Amadeus travelClass values differ from our internal enum.
_CABIN_MAP = {
    CabinClass.ECONOMY: "ECONOMY",
    CabinClass.PREMIUM_ECONOMY: "PREMIUM_ECONOMY",
    CabinClass.BUSINESS: "BUSINESS",
    CabinClass.FIRST: "FIRST",
}


class AmadeusProvider(HttpFlightProvider):
    name = "amadeus"

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(base_url=settings.amadeus_base_url)
        self._client_id = settings.amadeus_client_id
        self._client_secret = settings.amadeus_client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _access_token(self) -> str:
        """Fetch (and cache) an OAuth2 bearer token."""
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        if not self.configured:
            raise ProviderError("Amadeus credentials are not configured.")

        # The token endpoint requires a form-encoded body, so we call httpx
        # directly instead of the JSON-oriented _request helper.
        import httpx

        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._settings.provider_timeout_seconds
        ) as client:
            resp = await client.post(
                "/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Amadeus auth failed (HTTP {resp.status_code}).",
                details={"body": resp.text[:300]},
            )
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 1799))
        return self._token

    async def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._access_token()}",
            "Accept": "application/json",
        }

    # --- search ------------------------------------------------------------
    async def search(self, query: FlightSearchQuery) -> list[FlightOffer]:
        params: dict[str, object] = {
            "originLocationCode": query.origin.upper(),
            "destinationLocationCode": query.destination.upper(),
            "departureDate": query.departure_date,
            "adults": query.adults,
            "currencyCode": query.currency,
            "max": query.max_results,
            "travelClass": _CABIN_MAP.get(query.cabin, "ECONOMY"),
        }
        if query.return_date:
            params["returnDate"] = query.return_date
        if query.children:
            params["children"] = query.children
        if query.infants:
            params["infants"] = query.infants
        if query.non_stop:
            params["nonStop"] = "true"

        data = await self._request(
            "GET", "/v2/shopping/flight-offers", headers=await self._auth_headers(), params=params
        )
        offers = [self._to_offer(item) for item in (data or {}).get("data", [])]
        logger.info(
            "amadeus_search", count=len(offers), route=f"{query.origin}-{query.destination}"
        )
        return offers

    # --- price -------------------------------------------------------------
    async def price(self, offer: FlightOffer) -> FlightOffer:
        body = {
            "data": {
                "type": "flight-offers-pricing",
                "flightOffers": [offer.raw],
            }
        }
        data = await self._request(
            "POST",
            "/v1/shopping/flight-offers/pricing",
            headers=await self._auth_headers(),
            json=body,
        )
        priced = (data or {}).get("data", {}).get("flightOffers", [])
        if not priced:
            raise ProviderError("Amadeus returned no priced offer.")
        return mark_priced(self._to_offer(priced[0]), shopped_offer_id=offer.offer_id)

    # --- order -------------------------------------------------------------
    async def order(
        self,
        offer: FlightOffer,
        passengers: list[PassengerInfo],
        *,
        contact_email: str,
        contact_phone: str | None = None,
    ) -> OrderResult:
        traveller_payload = [self._to_traveller(p, i) for i, p in enumerate(passengers)]
        contact: dict[str, object] = {
            "emailAddress": contact_email,
            "address": {"countryCode": "VN"},
        }
        if contact_phone:
            contact["phones"] = [
                {"deviceType": "MOBILE", "countryCallingCode": "84", "number": contact_phone}
            ]

        body = {
            "data": {
                "type": "flight-order",
                "flightOffers": [offer.raw],
                "travelers": traveller_payload,
                "remarks": {"general": [{"subType": "GENERAL_MESSAGE", "text": "AirDesk booking"}]},
            }
        }
        data = await self._request(
            "POST", "/v1/booking/flight-orders", headers=await self._auth_headers(), json=body
        )
        order = (data or {}).get("data", {})
        tickets = self._extract_tickets(order)
        # The order echoes the priced offer back; reuse the same total the
        # search path reads (grandTotal, falling back to total) rather than
        # guessing at a bare field name.
        order_price = (order.get("flightOffers") or [{}])[0].get("price", {}) or {}
        order_total = order_price.get("grandTotal") or order_price.get("total") or "0"
        return OrderResult(
            provider=self.name,
            order_id=order.get("id", ""),
            booking_reference=(order.get("associatedRecords") or [{}])[0].get("reference", ""),
            status=order.get("queuingOfficeId", "confirmed") and "confirmed",
            total_amount=self._money_to_minor(order_total),
            currency=offer.currency,
            tickets=tickets,
            raw=order,
        )

    # --- mapping helpers ---------------------------------------------------
    @staticmethod
    def _to_traveller(p: PassengerInfo, idx: int) -> dict:
        traveller: dict[str, object] = {
            "id": str(idx + 1),
            "dateOfBirth": p.date_of_birth or "1990-01-01",
            "name": {"firstName": p.first_name.upper(), "lastName": p.last_name.upper()},
            "gender": (p.gender or "MALE").upper(),
            "contact": {
                "emailAddress": p.email,
                "phones": (
                    [{"deviceType": "MOBILE", "countryCallingCode": "84", "number": p.phone}]
                    if p.phone
                    else []
                ),
            },
        }
        if p.document_number:
            traveller["documents"] = [
                {
                    "documentType": (p.document_type or "PASSPORT").upper(),
                    "number": p.document_number,
                    "nationality": (p.nationality or "VN").upper(),
                    "expiryDate": p.document_expiry,
                }
            ]
        if p.passenger_type != "adult":
            traveller["associatedAdultId"] = "1"
        return traveller

    @staticmethod
    def _duration_to_minutes(duration: str) -> int:
        """Parse ISO-8601-ish 'PT2H30M' to minutes."""
        import re

        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration or "")
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        return hours * 60 + minutes

    def _to_offer(self, item: dict) -> FlightOffer:
        price = item.get("price", {})
        currency = price.get("currency", "VND")
        itineraries = item.get("itineraries", [])
        dict_carriers = item.get("dictionaries", {}).get("carriers", {}) or {}

        segments: list[Segment] = []
        for itin in itineraries:
            for seg in itin.get("segments", []):
                seg_carrier = seg.get("carrierCode", "")
                segments.append(
                    Segment(
                        origin=seg["departure"]["iataCode"],
                        destination=seg["arrival"]["iataCode"],
                        departure_at=datetime.fromisoformat(seg["departure"]["at"]),
                        arrival_at=datetime.fromisoformat(seg["arrival"]["at"]),
                        carrier_code=seg_carrier,
                        carrier_name=dict_carriers.get(seg_carrier, seg_carrier),
                        flight_number=f"{seg_carrier}{seg.get('number', '')}",
                        duration_minutes=self._duration_to_minutes(seg.get("duration", "")),
                        aircraft=seg.get("aircraft", {}).get("code"),
                        cabin=(seg.get("cabin") or "ECONOMY").lower(),
                        fare_basis=seg.get("fareBasis"),
                    )
                )
        grand = price.get("grandTotal") or price.get("total") or "0"
        base = price.get("base") or "0"
        return FlightOffer(
            provider=self.name,
            offer_id=item.get("id", ""),
            segments=segments,
            total_amount=self._money_to_minor(grand),
            base_amount=self._money_to_minor(base),
            taxes_amount=max(0, self._money_to_minor(grand) - self._money_to_minor(base)),
            currency=currency,
            seats_available=(item.get("numberOfBookableSeats") or None),
            fare_type=(item.get("pricingOptions") or {}).get("fareType", [None])[0]
            if item.get("pricingOptions")
            else None,
            baggage_allowance=self._baggage_from(item),
            raw=item,
        )

    @staticmethod
    def _baggage_from(item: dict) -> str | None:
        try:
            bags = item["travelerPricings"][0]["fareDetailsBySegment"][0]["includedCheckedBags"]
            if "weight" in bags:
                return f"{bags['weight']}{bags.get('weightUnit', 'KG')}"
            if "quantity" in bags:
                return f"{bags['quantity']} bag(s)"
        except (KeyError, IndexError, TypeError):
            return None
        return None

    @staticmethod
    def _money_to_minor(value: str | float) -> int:
        """VND is a zero-decimal currency; keep integer đồng."""
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_tickets(order: dict) -> list[dict]:
        tickets: list[dict] = []
        for traveller in order.get("travelers", []):
            for doc in traveller.get("documents", []):
                tickets.append(
                    {
                        "passenger": f"{traveller.get('name', {}).get('firstName', '')} "
                        f"{traveller.get('name', {}).get('lastName', '')}".strip(),
                        "document_type": doc.get("documentType"),
                        "number": doc.get("number"),
                        "status": doc.get("status"),
                    }
                )
        return tickets
