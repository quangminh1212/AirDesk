"""Booking, agent conversation and escalation schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import (
    Channel,
    ConversationStatus,
    EscalationStatus,
    MessageRole,
)
from app.models.booking import (
    BookingStatus,
    CabinClass,
    PassengerType,
    PaymentStatus,
    TicketStatus,
)


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reference: str
    provider: str
    provider_order_id: str | None = None
    pnr: str | None = None
    status: BookingStatus
    cabin_class: CabinClass
    origin: str
    destination: str
    departure_at: datetime
    return_at: datetime | None = None
    airline_code: str
    airline_name: str
    currency: str
    total_amount: int
    base_amount: int
    taxes_amount: int
    markup_amount: int = 0
    contact_email: str | None = None
    contact_phone: str | None = None
    payment_status: PaymentStatus
    created_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime | None = None


class OrderEventRead(BaseModel):
    """One entry in a booking's append-only order timeline (NDC OrderView)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    occurred_at: datetime
    actor: str
    from_status: str | None = None
    to_status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TicketRead(BaseModel):
    """One issued ticket document (the artefact the customer actually flies on).

    ``ticket_number`` is empty when the provider returned no document number;
    the ticket row is still kept so the order fact is not lost, and ``notes``
    carries the provider's own wording.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_number: str
    segment_index: int
    flight_number: str
    status: TicketStatus
    pnr: str | None = None
    fare_amount: int
    tax_amount: int
    issued_at: datetime | None = None
    voided_at: datetime | None = None
    refunded_at: datetime | None = None
    refund_amount: int
    notes: str | None = None


class BookingDetail(BookingRead):
    passengers: list[PassengerRead] = Field(default_factory=list)
    events: list[OrderEventRead] = Field(default_factory=list)
    tickets: list[TicketRead] = Field(default_factory=list)
    offer_snapshot: dict[str, Any] | None = None
    notes: str | None = None


class PassengerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    passenger_type: PassengerType
    first_name: str
    last_name: str
    date_of_birth: datetime | None = None
    nationality: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    email: str | None = None
    phone: str | None = None


class ToolCallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    success: bool
    error: str | None = None
    duration_ms: int


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: MessageRole
    content: str
    model: str | None = None
    confidence: float | None = None
    latency_ms: int = 0
    created_at: datetime
    tool_calls: list[ToolCallRead] = Field(default_factory=list)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: Channel
    status: ConversationStatus
    customer_ref: str | None = None
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead] = Field(default_factory=list)


class AgentReply(BaseModel):
    """Response returned to the caller after processing a trip request."""

    conversation_id: str
    reply: str
    intent: str | None = None
    confidence: float | None = None
    offers: list[dict[str, Any]] = Field(default_factory=list)
    booking: BookingRead | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    # Automated quality review of this turn (see ``app.agents.qa``).
    qa: dict[str, Any] | None = None


class EscalationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None = None
    booking_id: str | None = None
    reason: str
    details: dict[str, Any] | None = None
    status: EscalationStatus
    priority: int
    assigned_to_id: str | None = None
    resolution_note: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class EscalationResolve(BaseModel):
    resolution_note: str = Field(..., min_length=2, max_length=2000)
    action: str = Field("resolve", pattern="^(resolve|reject)$")


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str | None
    actor_type: str
    action: str
    entity_type: str
    entity_id: str | None
    after: dict[str, Any] | None = None
    created_at: datetime


BookingDetail.model_rebuild()
