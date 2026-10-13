"""Booking domain models: bookings, passengers, tickets, payments."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class BookingStatus(str, enum.Enum):
    """Lifecycle of a booking, aligned with the provider order states."""

    DRAFT = "draft"  # offer selected, not yet priced
    PENDING_APPROVAL = "pending_approval"  # awaiting human approval (HITL)
    CONFIRMED = "confirmed"  # order created at provider
    TICKETED = "ticketed"  # ticket numbers issued
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"  # offer expired before confirmation


class CabinClass(str, enum.Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class PassengerType(str, enum.Enum):
    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    FAILED = "failed"


class TicketStatus(str, enum.Enum):
    """Lifecycle of a single issued ticket document.

    Modelled as an enum rather than free text on purpose: a typo in a status
    string is indistinguishable from a legitimate value and silently corrupts
    downstream reporting (the same failure class as mis-cased enum input).
    """

    ISSUED = "issued"  # ticket number issued by the carrier
    REFUNDED = "refunded"  # refunded to the customer; coupon void
    VOID = "void"  # never used; cancelled before travel
    EXCHANGED = "exchanged"  # replaced by a new ticket (reissue)
    USED = "used"  # flown


class Booking(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_status_created", "status", "created_at"),
        Index("ix_bookings_pnr", "pnr"),
    )

    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)

    # Provider linkage
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    pnr: Mapped[str | None] = mapped_column(String(20), nullable=True)

    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=24),
        default=BookingStatus.DRAFT,
        nullable=False,
    )
    cabin_class: Mapped[CabinClass] = mapped_column(
        Enum(CabinClass, native_enum=False, length=20),
        default=CabinClass.ECONOMY,
        nullable=False,
    )

    # Itinerary (denormalised summary; full offer stored as JSON snapshot)
    origin: Mapped[str] = mapped_column(String(3), nullable=False)
    destination: Mapped[str] = mapped_column(String(3), nullable=False)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    return_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    airline_code: Mapped[str] = mapped_column(String(3), nullable=False, default="")
    airline_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    # Money — stored in minor units (VND has no decimals => amount == minor units)
    currency: Mapped[str] = mapped_column(String(3), default="VND", nullable=False)
    total_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    base_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    taxes_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    #: Agency markup/service fee included in ``total_amount`` (Duffel/Norba model).
    markup_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=12),
        default=PaymentStatus.PENDING,
        nullable=False,
    )

    #: Idempotency key from POST /bookings — a retried request returns this row.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(80), unique=True, nullable=True, index=True
    )

    #: Booking-level contact (agency reaches the customer about this order).
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Provenance & audit: which agent/user created it, raw provider snapshot
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    offer_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticketed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Deadline for an unpaid hold; the carrier releases the PNR afterwards.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    passengers: Mapped[list[Passenger]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    events: Mapped[list[OrderEventRecord]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="OrderEventRecord.occurred_at",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Booking {self.reference} {self.origin}->{self.destination} {self.status.value}>"


class OrderEventRecord(UUIDMixin, TimestampMixin, Base):
    """Append-only order timeline (mirrors Norba ``history[]`` / NDC OrderView).

    Every meaningful transition on a booking is recorded here with who did it
    and what the status changed from/to, giving a replayable audit trail that
    is independent of the generic ``audit_logs`` table.
    """

    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_booking_time", "booking_id", "occurred_at"),)

    booking_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    booking: Mapped[Booking] = relationship(back_populates="events")


class Passenger(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "passengers"

    booking_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    passenger_type: Mapped[PassengerType] = mapped_column(
        Enum(PassengerType, native_enum=False, length=10),
        default=PassengerType.ADULT,
        nullable=False,
    )
    # Fields are stored encrypted-at-rest at the DB layer when configured.
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(2), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frequent_flyer: Mapped[str | None] = mapped_column(String(64), nullable=True)

    booking: Mapped[Booking] = relationship(back_populates="passengers")


class Ticket(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tickets"

    booking_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="CASCADE"), index=True, nullable=False
    )
    passenger_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("passengers.id", ondelete="SET NULL"), nullable=True
    )
    ticket_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    segment_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flight_number: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, native_enum=False, length=20),
        default=TicketStatus.ISSUED,
        nullable=False,
    )
    #: Carrier PNR / record locator the ticket belongs to, when provided.
    pnr: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Money on this specific document, in minor units (VND => no decimals).
    fare_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # --- Document lifecycle timestamps -------------------------------------
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exchanged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Refundable value returned to the customer, in minor units.
    refund_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    #: Ticket number this document replaced (reissue chain), if any.
    exchanged_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Free-form note (refund reason, exchange fare difference, ...).
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    booking: Mapped[Booking] = relationship(back_populates="tickets")
