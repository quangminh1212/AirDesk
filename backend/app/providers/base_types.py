"""Provider-agnostic flight domain types.

Every flight provider (Amadeus, Duffel, Mock) is adapted to these dataclasses,
so the rest of the application never depends on a vendor's wire format.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class CabinClass(str, enum.Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class OfferKind(str, enum.Enum):
    """Which half of the shopping flow an offer is in.

    This mirrors the NDC / Duffel distinction between a *shopped* offer (what
    ``AirShopping`` returns) and a *priced* offer (what ``OfferPrice`` returns).
    Only a priced offer may be booked; a shopped offer id is refused at order
    creation, exactly as Norba/Duffel/Amadeus enforce.
    """

    SHOPPED = "shopped"  # returned by search(); reference-only, not bookable
    PRICED = "priced"  # returned by price(); carries a price guarantee


class OrderStatus(str, enum.Enum):
    """Airline-order lifecycle (copied from NDC OrderView + Norba).

    ``pending`` -> created but nothing confirmed by the carrier.
    ``confirmed`` -> the carrier holds the PNR (hold exists, may be unpaid).
    ``ticketed`` -> documents issued (``tickets[]`` non-empty).
    ``cancelled`` / ``refunded`` -> terminal states.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    TICKETED = "ticketed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class OrderEventType(str, enum.Enum):
    """Append-only order timeline event kinds (Norba ``event_type``)."""

    CREATED = "created"
    REPRICED = "repriced"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    REFRESHED = "refreshed"
    STATUS_CHANGED = "status_changed"


@dataclass(slots=True)
class FlightSearchQuery:
    """Normalised search request."""

    origin: str  # IATA, e.g. "SGN"
    destination: str  # IATA, e.g. "HAN"
    departure_date: str  # YYYY-MM-DD
    return_date: str | None = None
    adults: int = 1
    children: int = 0
    infants: int = 0
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = "VND"
    max_results: int = 20
    non_stop: bool = False

    @property
    def total_passengers(self) -> int:
        return self.adults + self.children + self.infants

    @property
    def trip_type(self) -> str:
        return "round_trip" if self.return_date else "one_way"


@dataclass(slots=True)
class Segment:
    """A single flight leg."""

    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    carrier_code: str
    carrier_name: str
    flight_number: str
    duration_minutes: int = 0
    aircraft: str | None = None
    cabin: str = "economy"
    fare_basis: str | None = None
    baggage_allowance: str | None = None


@dataclass(slots=True)
class FlightOffer:
    """An itinerary with pricing, provider-agnostic.

    Follows the NDC/Duffel offer model: an offer produced by ``search()`` is a
    *shopped* offer (reference only, ``kind=SHOPPED``); the one produced by
    ``price()`` is a *priced* offer (``kind=PRICED``) that carries a price
    guarantee (``price_guarantee_until``) and may be booked.
    """

    provider: str
    offer_id: str
    segments: list[Segment]
    total_amount: int  # in minor units of `currency`
    base_amount: int
    taxes_amount: int
    currency: str
    seats_available: int | None = None
    expires_at: datetime | None = None
    fare_type: str | None = None
    refundable: bool | None = None
    baggage_allowance: str | None = None
    raw: dict = field(default_factory=dict)

    # --- NDC-style offer metadata -----------------------------------------
    kind: OfferKind = OfferKind.SHOPPED
    #: ID of the shopped offer this priced offer was derived from (when priced).
    shopped_offer_id: str | None = None
    #: Provider's own price-guarantee deadline (NDC OfferExpirationDateTime).
    price_guarantee_until: datetime | None = None

    # --- Commercial breakdown (airline price + our margin) ----------------
    #: Agency markup/service fee included in ``total_amount`` (Duffel/Norba model).
    markup_amount: int = 0
    #: The pure airline fare (base + taxes), excluding our markup.
    airline_amount: int = 0

    # --- Derived helpers ---------------------------------------------------
    @property
    def origin(self) -> str:
        return self.segments[0].origin if self.segments else ""

    @property
    def destination(self) -> str:
        return self.segments[-1].destination if self.segments else ""

    @property
    def departure_at(self) -> datetime | None:
        return self.segments[0].departure_at if self.segments else None

    @property
    def return_at(self) -> datetime | None:
        return self.segments[-1].departure_at if len(self.segments) > 1 else None

    @property
    def primary_carrier(self) -> str:
        return self.segments[0].carrier_code if self.segments else ""

    @property
    def primary_carrier_name(self) -> str:
        return self.segments[0].carrier_name if self.segments else ""

    @property
    def total_duration_minutes(self) -> int:
        return sum(s.duration_minutes for s in self.segments)

    @property
    def stops(self) -> int:
        return max(0, len(self.segments) - 1)

    @property
    def is_bookable(self) -> bool:
        """Only a *priced* offer may be booked (NDC/Duffel rule)."""
        return self.kind is OfferKind.PRICED

    def price_guarantee_valid(self, *, now: datetime | None = None) -> bool:
        """True while the priced offer's guarantee window is still open."""
        if self.kind is not OfferKind.PRICED:
            return False
        if self.price_guarantee_until is None:
            return True
        return (now or datetime.now(self.price_guarantee_until.tzinfo)) < self.price_guarantee_until


@dataclass(slots=True)
class PassengerInfo:
    """Passenger details required to create an order."""

    passenger_type: str  # "adult" | "child" | "infant"
    first_name: str
    last_name: str
    date_of_birth: str | None = None  # YYYY-MM-DD
    gender: str | None = None
    nationality: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    document_expiry: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(slots=True)
class OrderEvent:
    """One entry in an order's append-only timeline (Norba ``history[]``)."""

    event_type: str  # OrderEventType value
    occurred_at: datetime
    actor: str = "system"  # agency | support | system
    from_status: str | None = None
    to_status: str | None = None
    payload: dict = field(default_factory=dict)


@dataclass(slots=True)
class OrderResult:
    """Result of a book/order operation.

    Shaped after the NDC ``OrderView`` / Duffel order: a status from
    ``OrderStatus``, a carrier PNR (``booking_reference``), ``tickets[]`` that
    only populate once documents are issued, an ``expires_at`` for unpaid
    holds, and a full ``history[]`` audit trail.
    """

    provider: str
    order_id: str
    booking_reference: str
    status: str
    total_amount: int
    currency: str
    tickets: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    #: NDC-style lifecycle status (OrderStatus value).
    order_status: str = OrderStatus.PENDING.value
    #: Deadline for an unpaid hold; the carrier releases it afterwards.
    expires_at: datetime | None = None
    #: Append-only timeline of everything that happened to this order.
    history: list[OrderEvent] = field(default_factory=list)
    #: Non-fatal issues the caller must still read (Norba ``warnings[]``).
    warnings: list[str] = field(default_factory=list)


def mark_priced(
    offer: FlightOffer,
    *,
    shopped_offer_id: str | None = None,
    guarantee_minutes: int = 20,
    markup_amount: int = 0,
) -> FlightOffer:
    """Turn a *shopped* offer into a *priced*, bookable offer.

    Shared by every provider's ``price()`` so the NDC/Duffel contract holds
    uniformly: a new ``offer_id`` (``..._P``), a price-guarantee deadline, and
    the airline-vs-markup breakdown filled in. Providers that read real
    carriers (Amadeus/Duffel) call this on the re-fetched offer.
    """
    from datetime import UTC, timedelta

    now = datetime.now(UTC)
    shopped_id = shopped_offer_id or offer.shopped_offer_id or offer.offer_id
    offer.kind = OfferKind.PRICED
    offer.shopped_offer_id = shopped_id
    offer.offer_id = f"{shopped_id}_P"
    offer.price_guarantee_until = now + timedelta(minutes=guarantee_minutes)
    offer.expires_at = offer.expires_at or now + timedelta(minutes=30)
    offer.markup_amount = markup_amount
    offer.airline_amount = offer.total_amount
    offer.total_amount = offer.total_amount + markup_amount
    return offer
