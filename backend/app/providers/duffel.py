"""Duffel flight provider (Duffel Flights API v2).

Duffel supports Vietnamese carriers including Vietnam Airlines, VietJet and
Bamboo. Flow: POST /air/offer_requests -> offer -> POST /air/orders.
Amounts from Duffel are decimal strings; we convert to minor units.
"""

from __future__ import annotations

from datetime import datetime

from app.core.config import get_settings
from app.core.errors import ProviderError
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

_CABIN_MAP = {
    CabinClass.ECONOMY: "economy",
    CabinClass.PREMIUM_ECONOMY: "premium_economy",
    CabinClass.BUSINESS: "business",
    CabinClass.FIRST: "first",
}


class DuffelProvider(HttpFlightProvider):
    name = "duffel"

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(base_url=settings.duffel_base_url)
        self._token = settings.duffel_access_token
        self._version = settings.duffel_version

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Duffel-Version": self._version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def search(self, query: FlightSearchQuery) -> list[FlightOffer]:
        slices = [
            {
                "origin": query.origin.upper(),
                "destination": query.destination.upper(),
                "departure_date": query.departure_date,
            }
        ]
        if query.return_date:
            slices.append(
                {
                    "origin": query.destination.upper(),
                    "destination": query.origin.upper(),
                    "departure_date": query.return_date,
                }
            )
        body = {
            "data": {
                "slices": slices,
                "passengers": (
                    [{"type": "adult"} for _ in range(query.adults)]
                    + [{"age": 8} for _ in range(query.children)]
                    + [{"age": 1} for _ in range(query.infants)]
                ),
                "cabin_class": _CABIN_MAP.get(query.cabin, "economy"),
            }
        }
        data = await self._request(
            "POST",
            "/air/offer_requests",
            headers=self._auth_headers(),
            json=body,
            params={"return_offers": "true"},
        )
        offers = (data or {}).get("data", {}).get("offers", [])
        return [self._to_offer(item) for item in offers[: query.max_results]]

    async def price(self, offer: FlightOffer) -> FlightOffer:
        # Duffel prices are authoritative at offer creation; re-fetch to confirm.
        data = await self._request(
            "GET",
            f"/air/offers/{offer.offer_id}",
            headers=self._auth_headers(),
            params={"return_available_services": "false"},
        )
        item = (data or {}).get("data")
        if not item:
            raise ProviderError("Duffel offer could not be re-priced.")
        return mark_priced(self._to_offer(item), shopped_offer_id=offer.offer_id)

    async def order(
        self,
        offer: FlightOffer,
        passengers: list[PassengerInfo],
        *,
        contact_email: str,
        contact_phone: str | None = None,
    ) -> OrderResult:
        pax_payload = []
        for p in passengers:
            entry: dict[str, object] = {
                "type": {"adult": "adult", "child": "child", "infant": "infant"}.get(
                    p.passenger_type, "adult"
                ),
                "given_name": p.first_name,
                "family_name": p.last_name,
                "gender": (p.gender or "m").lower()[:1],
                "email": p.email or contact_email,
            }
            if p.date_of_birth:
                entry["born_on"] = p.date_of_birth
            if p.phone or contact_phone:
                entry["phone_number"] = p.phone or contact_phone
            if p.document_number:
                entry["identity_documents"] = [
                    {
                        "type": "passport",
                        "unique_identifier": p.document_number,
                        "issuing_country_code": (p.nationality or "VN").upper(),
                        "expires_on": p.document_expiry or "2030-01-01",
                    }
                ]
            pax_payload.append(entry)

        body = {
            "data": {
                "type": "order",
                "selected_offers": [offer.offer_id],
                "passengers": pax_payload,
                "payments": [
                    {
                        "type": "balance",
                        "currency": offer.currency,
                        "amount": str(offer.total_amount),
                    }
                ],
            }
        }
        data = await self._request("POST", "/air/orders", headers=self._auth_headers(), json=body)
        order = (data or {}).get("data", {})
        tickets = [
            {
                "passenger": f"{doc.get('passenger_id', '')}",
                "document_type": doc.get("type"),
                "number": doc.get("unique_identifier"),
            }
            for doc in order.get("documents", [])
        ]
        return OrderResult(
            provider=self.name,
            order_id=order.get("id", ""),
            booking_reference=order.get("booking_reference", ""),
            status=order.get("live_mode", False) and "confirmed" or "test",
            total_amount=self._money_to_minor(order.get("total_amount", "0")),
            currency=order.get("total_currency", offer.currency),
            tickets=tickets,
            raw=order,
        )

    # --- mapping -----------------------------------------------------------
    def _to_offer(self, item: dict) -> FlightOffer:
        total = item.get("total_amount", "0")
        base = item.get("base_amount", total)
        currency = item.get("total_currency", "VND")

        segments: list[Segment] = []
        for slice_ in item.get("slices", []):
            for seg in slice_.get("segments", []):
                dep = seg["departing_at"].replace("Z", "+00:00")
                arr = seg["arriving_at"].replace("Z", "+00:00")
                carrier = seg.get("marketing_carrier", {}) or {}
                origin = (seg.get("origin") or {}).get("iata_code", "???")
                dest = (seg.get("destination") or {}).get("iata_code", "???")
                duration = 0
                if seg.get("duration"):
                    duration = self._iso_duration_minutes(seg["duration"])
                segments.append(
                    Segment(
                        origin=origin,
                        destination=dest,
                        departure_at=datetime.fromisoformat(dep),
                        arrival_at=datetime.fromisoformat(arr),
                        carrier_code=carrier.get("iata_code", ""),
                        carrier_name=carrier.get("name", ""),
                        flight_number=(
                            f"{carrier.get('iata_code', '')}"
                            f"{seg.get('marketing_carrier_flight_number', '')}"
                        ),
                        duration_minutes=duration,
                        aircraft=(seg.get("aircraft") or {}).get("name"),
                        cabin=(seg.get("passengers") or [{}])[0].get("cabin_class", "economy")
                        if seg.get("passengers")
                        else "economy",
                        fare_basis=(seg.get("passengers") or [{}])[0].get("fare_basis_code")
                        if seg.get("passengers")
                        else None,
                    )
                )
        return FlightOffer(
            provider=self.name,
            offer_id=item.get("id", ""),
            segments=segments,
            total_amount=self._money_to_minor(total),
            base_amount=self._money_to_minor(base),
            taxes_amount=max(0, self._money_to_minor(total) - self._money_to_minor(base)),
            currency=currency,
            expires_at=(
                datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
                if item.get("expires_at")
                else None
            ),
            refundable=(item.get("conditions") or {})
            .get("refund_before_departure", {})
            .get("allowed")
            if item.get("conditions")
            else None,
            raw=item,
        )

    @staticmethod
    def _iso_duration_minutes(duration: str) -> int:
        import re

        match = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", duration or "")
        if not match:
            return 0
        days = int(match.group(1) or 0)
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)
        return days * 24 * 60 + hours * 60 + minutes

    @staticmethod
    def _money_to_minor(value: str | float) -> int:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return 0
