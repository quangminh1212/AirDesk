"""Flight search / offer / order schemas (API surface)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.providers.base_types import CabinClass


class FlightSearchRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3, description="IATA origin, e.g. SGN")
    destination: str = Field(..., min_length=3, max_length=3, description="IATA destination")
    departure_date: date
    return_date: date | None = None
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=8)
    infants: int = Field(0, ge=0, le=4)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = Field("VND", min_length=3, max_length=3)
    non_stop: bool = False
    max_results: int = Field(20, ge=1, le=50)
    provider: str = Field("auto", description="auto | mock | amadeus | duffel")

    @field_validator("origin", "destination")
    @classmethod
    def _upper_iata(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("return_date")
    @classmethod
    def _check_return(cls, v: date | None, info) -> date | None:
        dep = info.data.get("departure_date")
        if v and dep and v < dep:
            raise ValueError("return_date must be on/after departure_date")
        return v


class SegmentSchema(BaseModel):
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    carrier_code: str
    carrier_name: str
    flight_number: str
    duration_minutes: int
    aircraft: str | None = None
    cabin: str
    fare_basis: str | None = None
    baggage_allowance: str | None = None


class FlightOfferSchema(BaseModel):
    offer_id: str
    provider: str
    origin: str
    destination: str
    departure_at: datetime | None
    return_at: datetime | None
    airline_code: str
    airline_name: str
    segments: list[SegmentSchema]
    total_amount: int
    base_amount: int
    taxes_amount: int
    currency: str
    stops: int
    total_duration_minutes: int
    seats_available: int | None = None
    fare_type: str | None = None
    refundable: bool | None = None
    baggage_allowance: str | None = None
    expires_at: datetime | None = None
    # --- NDC-style offer metadata -----------------------------------------
    #: "shopped" (reference only) or "priced" (bookable). Mirrors NDC/Duffel.
    kind: str = "shopped"
    #: Price-guarantee deadline for a priced offer.
    price_guarantee_until: datetime | None = None
    #: Whether this offer may be booked (only priced, unexpired offers can).
    is_bookable: bool = False
    #: Agency markup/service fee included in ``total_amount``.
    markup_amount: int = 0
    #: The pure airline fare (base + taxes), excluding our markup.
    airline_amount: int = 0


class PassengerSchema(BaseModel):
    passenger_type: str = Field("adult", pattern="^(adult|child|infant)$")
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    date_of_birth: str | None = None
    gender: str | None = None
    nationality: str | None = Field(None, max_length=2)
    document_type: str | None = None
    document_number: str | None = None
    document_expiry: str | None = None
    email: str | None = None
    phone: str | None = None


class BookingCreateRequest(BaseModel):
    offer_id: str
    provider: str
    passengers: list[PassengerSchema] = Field(..., min_length=1)
    contact_email: str
    contact_phone: str | None = None
    cabin: CabinClass = CabinClass.ECONOMY


class TripRequest(BaseModel):
    """High-level natural-language trip request handled by the agent."""

    message: str = Field(..., min_length=2, max_length=2000)
    conversation_id: str | None = None
    channel: str = "web"
