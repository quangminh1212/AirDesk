"""Mock flight provider: deterministic, offline, Vietnamese carriers.

This provider always works with no credentials and no network, generating
realistic itineraries for the domestic Vietnamese market. It exists so the
whole system (agents, web view, tests, demos) runs end-to-end out of the box,
and it is the fixture used by the automated test-suite.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta

from app.providers.base import FlightProvider
from app.providers.base_types import (
    CabinClass,
    FlightOffer,
    FlightSearchQuery,
    OfferKind,
    OrderEvent,
    OrderEventType,
    OrderResult,
    OrderStatus,
    PassengerInfo,
    Segment,
)

#: Agency markup applied on top of the airline fare (pass-through + fee).
DEFAULT_MARKUP_RATE = 0.0

# Vietnamese carriers with representative IATA codes / names / base fares.
CARRIERS = [
    ("VN", "Vietnam Airlines", 1.0),
    ("VJ", "VietJet Air", 0.7),
    ("QH", "Bamboo Airways", 0.8),
    ("VU", "Vietravel Airlines", 0.75),
    ("9G", "Pacific Airlines", 0.65),
]

# Major Vietnamese airports with coordinates for plausible durations.
AIRPORTS = {
    "SGN": ("Ho Chi Minh City", 10.8188, 106.6520),
    "HAN": ("Hanoi", 21.2212, 105.8072),
    "DAD": ("Da Nang", 16.0439, 108.1994),
    "PQC": ("Phu Quoc", 10.1698, 103.9931),
    "CXR": ("Nha Trang", 12.2275, 109.1926),
    "DLI": ("Da Lat", 11.7503, 108.3739),
    "HUI": ("Hue", 16.4015, 107.7029),
    "VDO": ("Van Don", 21.1177, 107.4145),
    "HPH": ("Hai Phong", 20.8194, 106.7244),
    "VII": ("Vinh", 18.7373, 105.6708),
    "UIH": ("Quy Nhon", 13.9550, 109.0423),
    "VCA": ("Can Tho", 10.0851, 105.7118),
}

_AIRCRAFT = ["Airbus A321", "Airbus A320neo", "Boeing 787-9", "Airbus A350-900", "ATR 72"]


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


class MockFlightProvider(FlightProvider):
    name = "mock"

    @property
    def configured(self) -> bool:
        return True

    def _rng(self, query: FlightSearchQuery, salt: str = "") -> random.Random:
        """Deterministic RNG so the same query yields stable results."""
        seed_src = (
            f"{query.origin}{query.destination}{query.departure_date}"
            f"{query.return_date}{query.adults}{query.cabin.value}{salt}"
        )
        digest = hashlib.sha256(seed_src.encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def _distance_km(self, origin: str, dest: str) -> float:
        o = AIRPORTS.get(origin.upper())
        d = AIRPORTS.get(dest.upper())
        if not o or not d:
            return 1000.0
        return _haversine_km((o[1], o[2]), (d[1], d[2]))

    async def search(self, query: FlightSearchQuery) -> list[FlightOffer]:
        rng = self._rng(query)
        distance = self._distance_km(query.origin, query.destination)
        base_minutes = int(distance / 12) + 35  # ~800km/h + taxi
        base_fare = int(distance * rng.uniform(1400, 2200))  # VND per km

        offers: list[FlightOffer] = []
        depart_date = datetime.fromisoformat(query.departure_date).replace(tzinfo=UTC)
        cabin_mult = {
            CabinClass.ECONOMY: 1.0,
            CabinClass.PREMIUM_ECONOMY: 1.6,
            CabinClass.BUSINESS: 2.8,
            CabinClass.FIRST: 4.5,
        }[query.cabin]

        n_offers = min(query.max_results, rng.randint(4, 8))
        for i in range(n_offers):
            code, cname, price_factor = rng.choice(CARRIERS)
            dep_hour = rng.randint(5, 21)
            dep_min = rng.choice([0, 15, 30, 45])
            departure = depart_date.replace(hour=dep_hour, minute=dep_min)
            duration = base_minutes + rng.randint(-10, 35)
            arrival = departure + timedelta(minutes=duration)

            total = int(base_fare * price_factor * cabin_mult * rng.uniform(0.9, 1.15))
            total = int(round(total / 1000) * 1000)  # round to nearest 1,000 VND
            base = int(total * 0.82)

            offers.append(
                FlightOffer(
                    provider=self.name,
                    offer_id=f"mock_{query.origin}{query.destination}_{i}_{code}",
                    segments=[
                        Segment(
                            origin=query.origin.upper(),
                            destination=query.destination.upper(),
                            departure_at=departure,
                            arrival_at=arrival,
                            carrier_code=code,
                            carrier_name=cname,
                            flight_number=f"{code}{rng.randint(100, 999)}",
                            duration_minutes=duration,
                            aircraft=rng.choice(_AIRCRAFT),
                            cabin=query.cabin.value,
                            fare_basis=f"{code}MOCK{rng.randint(1, 9)}",
                            baggage_allowance=rng.choice(["7kg", "20kg", "23kg", "32kg"]),
                        )
                    ],
                    total_amount=total,
                    base_amount=base,
                    taxes_amount=total - base,
                    currency=query.currency,
                    seats_available=rng.randint(1, 9),
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    fare_type=rng.choice(["ECONOMY_STANDARD", "ECONOMY_FLEX", "PROMO"]),
                    refundable=rng.random() < 0.4,
                    baggage_allowance=rng.choice(["7kg", "20kg", "23kg"]),
                    raw={"mock": True, "index": i},
                )
            )
        offers.sort(key=lambda o: o.total_amount)
        return offers

    async def price(self, offer: FlightOffer) -> FlightOffer:
        """NDC ``OfferPrice``: issue a priced offer with a fresh guarantee.

        The shopped offer id is kept as the link, a new ``offer_id`` is minted
        with a ``_P`` suffix, the price guarantee window is set, and the
        airline-vs-markup breakdown is filled in.
        """
        shopped_id = offer.shopped_offer_id or offer.offer_id
        now = datetime.now(UTC)
        markup = max(0, int(offer.total_amount * DEFAULT_MARKUP_RATE))
        return FlightOffer(
            provider=offer.provider,
            offer_id=f"{shopped_id}_P",
            segments=offer.segments,
            total_amount=offer.total_amount + markup,
            base_amount=offer.base_amount,
            taxes_amount=offer.taxes_amount,
            currency=offer.currency,
            seats_available=offer.seats_available,
            expires_at=now + timedelta(minutes=30),
            fare_type=offer.fare_type,
            refundable=offer.refundable,
            baggage_allowance=offer.baggage_allowance,
            raw={**offer.raw, "repriced_at": now.isoformat()},
            kind=OfferKind.PRICED,
            shopped_offer_id=shopped_id,
            price_guarantee_until=now + timedelta(minutes=20),
            markup_amount=markup,
            airline_amount=offer.total_amount,
        )

    async def order(
        self,
        offer: FlightOffer,
        passengers: list[PassengerInfo],
        *,
        contact_email: str,
        contact_phone: str | None = None,
    ) -> OrderResult:
        rng = self._rng(
            FlightSearchQuery(
                origin=offer.origin or "SGN",
                destination=offer.destination or "HAN",
                departure_date="2025-01-01",
            ),
            salt=offer.offer_id,
        )
        pnr = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(6))
        order_id = f"ord_mock_{rng.getrandbits(48):012x}"
        tickets = []
        for i, pax in enumerate(passengers):
            tickets.append(
                {
                    "passenger": f"{pax.first_name} {pax.last_name}".strip(),
                    "document_type": "TICKET",
                    "number": (
                        f"{offer.primary_carrier or 'VN'}-{rng.randint(1000000000, 9999999999)}"
                    ),
                    "segment_index": i,
                    "status": "issued",
                }
            )
        now = datetime.now(UTC)
        return OrderResult(
            provider=self.name,
            order_id=order_id,
            booking_reference=pnr,
            status="confirmed",
            total_amount=offer.total_amount,
            currency=offer.currency,
            tickets=tickets,
            raw={
                "mock": True,
                "pnr": pnr,
                "contact_email": contact_email,
                "contact_phone": contact_phone,
            },
            order_status=OrderStatus.CONFIRMED.value,
            expires_at=now + timedelta(minutes=30),
            history=[
                OrderEvent(
                    event_type=OrderEventType.CREATED.value,
                    occurred_at=now,
                    actor="system",
                ),
                OrderEvent(
                    event_type=OrderEventType.COMMITTED.value,
                    occurred_at=now,
                    actor="system",
                    from_status=OrderStatus.PENDING.value,
                    to_status=OrderStatus.CONFIRMED.value,
                    payload={"pnr": pnr},
                ),
            ],
        )
