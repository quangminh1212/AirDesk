"""Shipment schemas (create / read / tracking / stats).

Follows the same shape as ``app.schemas.task`` and ``app.schemas.booking``:
a strict create payload, a partial update payload, an ORM-backed read model,
and small histogram models that always contain every bucket so the frontend
never has to guess which keys exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.shipment import ShipmentMode, ShipmentPriority, ShipmentStatus


class ShipmentCreate(BaseModel):
    """Payload for ``POST /shipments``."""

    origin: str = Field(..., min_length=2, max_length=120)
    destination: str = Field(..., min_length=2, max_length=120)
    mode: ShipmentMode = ShipmentMode.AIR
    priority: int = Field(
        ShipmentPriority.NORMAL.value, ge=1, le=3, description="1 = urgent, 2 = normal, 3 = economy"
    )
    carrier: str | None = Field(None, max_length=120)
    customer_name: str | None = Field(None, max_length=160)
    customer_phone: str | None = Field(None, max_length=32)
    customer_email: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    pieces: int = Field(1, ge=1, le=10_000)
    weight_kg: float | None = Field(None, ge=0, le=1_000_000)
    declared_value: int = Field(0, ge=0, le=10**15)
    cod_amount: int = Field(0, ge=0, le=10**15)
    currency: str = Field("VND", min_length=3, max_length=3)
    requires_customs: bool = False
    is_dangerous_goods: bool = False
    eta_at: datetime | None = None
    conversation_id: str | None = Field(None, max_length=36)
    booking_id: str | None = Field(None, max_length=36)
    notes: str | None = Field(None, max_length=10_000)
    meta: dict[str, Any] | None = None
    idempotency_key: str | None = Field(None, max_length=80)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()


class ShipmentStatusUpdate(BaseModel):
    """Payload for ``POST /shipments/{id}/status``."""

    status: ShipmentStatus
    location: str | None = Field(None, max_length=160)
    note: str | None = Field(None, max_length=2_000)


class TrackingEventCreate(BaseModel):
    """Payload for ``POST /shipments/{id}/events`` (a carrier scan)."""

    event_type: str = Field(..., min_length=2, max_length=32)
    location: str | None = Field(None, max_length=160)
    note: str | None = Field(None, max_length=2_000)
    occurred_at: datetime | None = None
    payload: dict[str, Any] | None = None


class ShipmentCancel(BaseModel):
    """Payload for ``POST /shipments/{id}/cancel``."""

    reason: str = Field(..., min_length=2, max_length=2_000)


class ShipmentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    occurred_at: datetime
    actor: str
    location: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ShipmentRead(BaseModel):
    """Full shipment record including the tracking timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    reference: str
    status: ShipmentStatus
    mode: ShipmentMode
    priority: int
    carrier: str | None = None
    tracking_number: str | None = None
    waybill_number: str | None = None
    origin: str
    destination: str
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    description: str | None = None
    pieces: int
    weight_kg: float | None = None
    declared_value: int
    cod_amount: int
    currency: str
    requires_customs: bool
    is_dangerous_goods: bool
    picked_up_at: datetime | None = None
    eta_at: datetime | None = None
    delivered_at: datetime | None = None
    created_by_id: str | None = None
    conversation_id: str | None = None
    booking_id: str | None = None
    notes: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    #: Derived: open past its promised ETA.
    is_overdue: bool = False
    #: Derived: statuses this shipment may legally move to next.
    allowed_next: list[str] = Field(default_factory=list)
    events: list[ShipmentEventRead] = Field(default_factory=list)


class ShipmentSummary(BaseModel):
    """Compact row for list views (no timeline)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    reference: str
    status: ShipmentStatus
    mode: ShipmentMode
    priority: int
    carrier: str | None = None
    tracking_number: str | None = None
    origin: str
    destination: str
    customer_name: str | None = None
    pieces: int
    weight_kg: float | None = None
    eta_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    is_overdue: bool = False


class ShipmentStatusCount(BaseModel):
    """One bucket of the by-status histogram (every status always present)."""

    status: ShipmentStatus
    count: int = 0


class ShipmentModeCount(BaseModel):
    """One bucket of the by-mode histogram (every mode always present)."""

    mode: ShipmentMode
    count: int = 0


class ShipmentStats(BaseModel):
    """Aggregated logistics health for the operations dashboard."""

    total: int = 0
    by_status: list[ShipmentStatusCount] = Field(default_factory=list)
    by_mode: list[ShipmentModeCount] = Field(default_factory=list)
    in_transit: int = 0
    needs_attention: int = 0
    overdue: int = 0
    delivered_this_week: int = 0
    total_declared_value_vnd: int = 0
