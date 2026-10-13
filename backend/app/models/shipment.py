"""Logistics domain models: shipments and their tracking timeline.

The agency does not only sell air tickets — it also moves cargo and documents
for customers (mẫu hàng, chứng từ, hành lý ký gửi quá cước). ``Shipment`` is the
operator's view of one such consignment; ``ShipmentEvent`` is the append-only
tracking timeline, mirroring ``OrderEventRecord`` for bookings so an auditor can
replay exactly how a shipment moved.

Money is stored in minor units for the same reason as bookings: VND has no
decimals, so 1 VND == 1 minor unit and no float ever touches a price.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class ShipmentStatus(str, enum.Enum):
    """Lifecycle of a consignment, aligned with common carrier statuses."""

    DRAFT = "draft"  # created internally, not yet handed to a carrier
    BOOKED = "booked"  # space/waybill reserved at the carrier
    PICKED_UP = "picked_up"  # carrier collected the goods
    IN_TRANSIT = "in_transit"
    CUSTOMS = "customs"  # held at customs clearance
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    DELAYED = "delayed"  # needs operator attention
    EXCEPTION = "exception"  # damage/loss/rejection — needs human handling
    CANCELLED = "cancelled"
    RETURNED = "returned"


class ShipmentMode(str, enum.Enum):
    """How the consignment physically travels."""

    AIR = "air"
    SEA = "sea"
    ROAD = "road"
    RAIL = "rail"
    COURIER = "courier"


class ShipmentPriority(int, enum.Enum):
    """Handling urgency, mirroring the 1=high .. 3=low convention."""

    URGENT = 1
    NORMAL = 2
    ECONOMY = 3


#: Statuses that mean the operator no longer has to push the shipment forward.
CLOSED_SHIPMENT_STATUSES: tuple[ShipmentStatus, ...] = (
    ShipmentStatus.DELIVERED,
    ShipmentStatus.CANCELLED,
    ShipmentStatus.RETURNED,
)

#: Statuses that mean something is wrong and a human should look at it.
ATTENTION_SHIPMENT_STATUSES: tuple[ShipmentStatus, ...] = (
    ShipmentStatus.DELAYED,
    ShipmentStatus.EXCEPTION,
    ShipmentStatus.CUSTOMS,
)


class Shipment(UUIDMixin, TimestampMixin, Base):
    """One consignment the agency is moving for a customer."""

    __tablename__ = "shipments"
    __table_args__ = (
        Index("ix_shipments_status_eta", "status", "eta_at"),
        Index("ix_shipments_tracking", "tracking_number"),
    )

    reference: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)

    status: Mapped[ShipmentStatus] = mapped_column(
        Enum(ShipmentStatus, native_enum=False, length=20),
        default=ShipmentStatus.DRAFT,
        nullable=False,
    )
    mode: Mapped[ShipmentMode] = mapped_column(
        Enum(ShipmentMode, native_enum=False, length=12),
        default=ShipmentMode.AIR,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=ShipmentPriority.NORMAL.value, nullable=False
    )

    # Carrier linkage. ``carrier`` is free text because the agency buys from a
    # rotating set of forwarders; ``tracking_number`` is whatever they hand back.
    carrier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    waybill_number: Mapped[str | None] = mapped_column(String(80), nullable=True)

    origin: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    destination: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    #: Denormalised customer contact so a shipment is readable without a booking.
    customer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Count of pieces (kiện) in the consignment.
    pieces: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Total chargeable weight in kilograms. ``Float`` (not ``Numeric``) keeps the
    #: column identical on Postgres and SQLite; weights are never used for money.
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Declared customs value and cash-on-delivery amount, in minor units.
    declared_value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cod_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="VND", nullable=False)

    #: True when the goods clear customs / require docs — drives extra checks.
    requires_customs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_dangerous_goods: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Estimated time of arrival promised to the customer.
    eta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provenance links, mirroring Booking/Task.
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    booking_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Idempotency key from POST /shipments — a retried request returns this row.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(80), unique=True, nullable=True, index=True
    )
    #: Free-form carrier payload (quote, label URL, last poll response, ...).
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    events: Mapped[list[ShipmentEvent]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.occurred_at",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Shipment {self.reference} {self.origin}->{self.destination} {self.status.value}>"


class ShipmentEvent(UUIDMixin, TimestampMixin, Base):
    """Append-only tracking timeline entry for a shipment.

    Mirrors ``OrderEventRecord``: every status transition (whether typed by an
    operator, scraped from a carrier, or produced by the agent) lands here with
    the actor and the from/to status, so the shipment's history is replayable
    independently of the generic ``audit_logs`` table.
    """

    __tablename__ = "shipment_events"
    __table_args__ = (Index("ix_shipment_events_shipment_time", "shipment_id", "occurred_at"),)

    shipment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shipments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: "agent" | "operator" | "carrier" | "system"
    actor: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    shipment: Mapped[Shipment] = relationship(back_populates="events")
