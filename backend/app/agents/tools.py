"""Agent tool definitions and execution.

Tools are declared in the OpenAI/LiteLLM function-calling schema and
implemented against the provider registry and booking service. Every tool
execution is recorded (name, arguments, result, duration, success) so the
system can audit what an agent did and feed failures back to a human.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.booking import CabinClass as ModelCabinClass
from app.models.shipment import ShipmentMode, ShipmentStatus
from app.providers.base_types import CabinClass, FlightSearchQuery
from app.providers.registry import available_provider_names, get_provider, search_flights
from app.services.booking import BookingService
from app.services.shipment import ShipmentService

logger = get_logger(__name__)


def _parse_shipment_mode(value: str) -> ShipmentMode:
    """Accept 'air', 'AIR', 'Air' - LLMs are inconsistent about casing."""
    try:
        return ShipmentMode(value.strip().lower())
    except ValueError as exc:
        valid = ", ".join(m.value for m in ShipmentMode)
        raise AppError(f"Unknown shipment mode '{value}'. Valid modes: {valid}.") from exc


def _parse_shipment_status(value: str) -> ShipmentStatus:
    try:
        return ShipmentStatus(value.strip().lower())
    except ValueError as exc:
        valid = ", ".join(s.value for s in ShipmentStatus)
        raise AppError(f"Unknown shipment status '{value}'. Valid statuses: {valid}.") from exc


@dataclass
class ToolOutcome:
    """Result of running a single tool."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    duration_ms: int = 0


# --- JSON schema declarations (LiteLLM / OpenAI function-calling format) ----
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": (
                "Search available flights between two Vietnamese (or international) "
                "airports. Returns a list of offers with prices in VND. Use IATA "
                "airport codes: SGN=Ho Chi Minh, HAN=Hanoi, DAD=Da Nang, PQC=Phu Quoc, "
                "CXR=Nha Trang, DLI=Da Lat, HUI=Hue, HPH=Hai Phong, VII=Vinh, UIH=Quy Nhon."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Origin IATA code, e.g. SGN"},
                    "destination": {"type": "string", "description": "Destination IATA code"},
                    "departure_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "return_date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
                    "adults": {"type": "integer", "minimum": 1, "default": 1},
                    "children": {"type": "integer", "minimum": 0, "default": 0},
                    "infants": {"type": "integer", "minimum": 0, "default": 0},
                    "cabin": {
                        "type": "string",
                        "enum": ["economy", "premium_economy", "business", "first"],
                        "default": "economy",
                    },
                    "currency": {"type": "string", "default": "VND"},
                },
                "required": ["origin", "destination", "departure_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_offer_details",
            "description": "Re-price and confirm availability of a specific flight offer by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "offer_id": {"type": "string"},
                },
                "required": ["provider", "offer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": (
                "Create a booking for a chosen offer. Requires passenger details and a "
                "contact email. Expensive bookings are routed to human approval "
                "automatically. Always confirm details with the customer before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "offer_id": {"type": "string"},
                    "contact_email": {"type": "string"},
                    "contact_phone": {"type": "string"},
                    "passengers": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "passenger_type": {
                                    "type": "string",
                                    "enum": ["adult", "child", "infant"],
                                    "default": "adult",
                                },
                                "first_name": {"type": "string"},
                                "last_name": {"type": "string"},
                                "date_of_birth": {"type": "string"},
                                "gender": {"type": "string"},
                                "nationality": {"type": "string"},
                                "document_type": {"type": "string"},
                                "document_number": {"type": "string"},
                                "email": {"type": "string"},
                                "phone": {"type": "string"},
                            },
                            "required": ["first_name", "last_name"],
                        },
                    },
                    "cabin": {"type": "string", "default": "economy"},
                },
                "required": ["provider", "offer_id", "contact_email", "passengers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Hand the conversation to a human operator when you are unsure, the "
                "customer asks for something outside policy, or the request is ambiguous."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_providers",
            "description": "List the flight inventory providers currently available.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_shipment",
            "description": (
                "Look up a cargo/logistics consignment and return its current status and "
                "full tracking timeline. Accepts a tracking number, waybill number, or our "
                "own shipment reference (e.g. SHP-8KQ2ZP). Use this whenever a customer asks "
                "'hàng của tôi đang ở đâu' / 'where is my shipment'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "Tracking number, waybill number, or shipment reference.",
                    }
                },
                "required": ["tracking_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_shipment",
            "description": (
                "Register a new consignment (cargo, documents, or oversize baggage) that the "
                "agency will move for the customer. Dangerous goods and high-value "
                "consignments are routed to a human operator for approval automatically. "
                "Always confirm origin, destination and piece count with the customer first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Pickup city/airport"},
                    "destination": {"type": "string", "description": "Delivery city/airport"},
                    "mode": {
                        "type": "string",
                        "enum": ["air", "sea", "road", "rail", "courier"],
                        "default": "air",
                    },
                    "priority": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                        "default": 2,
                        "description": "1 = urgent, 2 = normal, 3 = economy.",
                    },
                    "description": {"type": "string", "description": "What is inside the box."},
                    "pieces": {"type": "integer", "minimum": 1, "default": 1},
                    "weight_kg": {"type": "number", "description": "Total weight in kilograms."},
                    "declared_value": {
                        "type": "integer",
                        "description": "Declared value in VND minor units (1 VND = 1 unit).",
                    },
                    "cod_amount": {
                        "type": "integer",
                        "description": "Cash-on-delivery amount in VND, if collected.",
                    },
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "customer_email": {"type": "string"},
                    "requires_customs": {"type": "boolean", "default": False},
                    "is_dangerous_goods": {"type": "boolean", "default": False},
                    "notes": {"type": "string"},
                },
                "required": ["origin", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_shipment_status",
            "description": (
                "Advance a consignment to a new status when the customer or carrier reports "
                "progress (e.g. picked up, in transit, delivered). The status machine rejects "
                "impossible jumps; DELAYED/EXCEPTION/CUSTOMS automatically open an operator "
                "task. Always quote the shipment reference when confirming to the customer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "Tracking number, waybill number, or shipment reference.",
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "draft",
                            "booked",
                            "picked_up",
                            "in_transit",
                            "customs",
                            "out_for_delivery",
                            "delivered",
                            "delayed",
                            "exception",
                            "cancelled",
                            "returned",
                        ],
                    },
                    "location": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["tracking_number", "status"],
            },
        },
    },
]


class ToolRuntime:
    """Executes agent tools against real services within a DB session."""

    def __init__(
        self, db: AsyncSession, *, conversation_id: str | None, actor_id: str | None
    ) -> None:
        self._db = db
        self._conversation_id = conversation_id
        self._actor_id = actor_id
        self._bookings = BookingService(db)
        self._shipments = ShipmentService(db)
        # Cache of offers seen this turn so create_booking can resolve offer_id.
        self.offer_cache: dict[str, Any] = {}

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        outcome = ToolOutcome(name=name, arguments=arguments)
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                raise AppError(f"Unknown tool '{name}'.")
            outcome.result = await handler(**arguments)
            outcome.success = True
        except Exception as exc:  # noqa: BLE001 - surface any tool error to the agent/human
            outcome.success = False
            outcome.error = str(exc)
            outcome.result = {"error": str(exc)}
            logger.warning("tool_failed", tool=name, error=str(exc))
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # --- handlers ----------------------------------------------------------
    async def _tool_search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        adults: int = 1,
        children: int = 0,
        infants: int = 0,
        cabin: str = "economy",
        currency: str = "VND",
    ) -> dict[str, Any]:
        query = FlightSearchQuery(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children=children,
            infants=infants,
            cabin=CabinClass(cabin),
            currency=currency,
            max_results=10,
        )
        offers = await search_flights(query)
        self.offer_cache.update({f"{o.provider}:{o.offer_id}": o for o in offers})
        self.offer_cache.update({o.offer_id: o for o in offers})
        return {
            "count": len(offers),
            "offers": [
                {
                    "offer_id": o.offer_id,
                    "provider": o.provider,
                    "airline": f"{o.primary_carrier} {o.primary_carrier_name}",
                    "route": f"{o.origin}-{o.destination}",
                    "departure": o.departure_at.isoformat() if o.departure_at else None,
                    "stops": o.stops,
                    "duration_minutes": o.total_duration_minutes,
                    "total_amount": o.total_amount,
                    "currency": o.currency,
                }
                for o in offers[:10]
            ],
        }

    async def _tool_get_offer_details(self, provider: str, offer_id: str) -> dict[str, Any]:
        cached = self.offer_cache.get(f"{provider}:{offer_id}") or self.offer_cache.get(offer_id)
        p = get_provider(provider)
        if cached is not None:
            priced = await p.price(cached)
        else:
            # Without the raw offer we cannot re-price; report clearly.
            return {"error": "offer not found in this session; search again first"}
        return {
            "offer_id": priced.offer_id,
            "provider": priced.provider,
            "total_amount": priced.total_amount,
            "currency": priced.currency,
            "seats_available": priced.seats_available,
        }

    async def _tool_create_booking(
        self,
        provider: str,
        offer_id: str,
        contact_email: str,
        passengers: list[dict[str, Any]],
        contact_phone: str | None = None,
        cabin: str = "economy",
    ) -> dict[str, Any]:
        from app.schemas.flight import PassengerSchema

        offer = self.offer_cache.get(f"{provider}:{offer_id}") or self.offer_cache.get(offer_id)
        if offer is None:
            return {"error": "offer not found in this session; search again first"}

        pax = [PassengerSchema(**p) for p in passengers]
        booking = await self._bookings.create_booking(
            offer=offer,
            passengers=pax,
            contact_email=contact_email,
            contact_phone=contact_phone,
            created_by_id=self._actor_id,
            conversation_id=self._conversation_id,
            # `cabin` arrives as a plain string from the model; the service
            # persists the model-layer enum, not the provider-layer one.
            cabin=ModelCabinClass(cabin),
            actor_type="agent",
        )
        return {
            "booking_id": booking.id,
            "reference": booking.reference,
            "status": booking.status.value,
            "pnr": booking.pnr,
            "total_amount": booking.total_amount,
            "currency": booking.currency,
            "requires_approval": booking.status.value == "pending_approval",
        }

    async def _tool_escalate_to_human(
        self,
        reason: str,
        priority: int = 2,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.models.agent import Conversation, ConversationStatus, Escalation, EscalationStatus

        if self._conversation_id:
            convo = await self._db.get(Conversation, self._conversation_id)
            if convo:
                convo.status = ConversationStatus.PENDING_HUMAN
        escalation = Escalation(
            conversation_id=self._conversation_id,
            reason=reason,
            priority=priority,
            status=EscalationStatus.OPEN,
            details=details,
        )
        self._db.add(escalation)
        await self._db.flush()
        return {"escalation_id": escalation.id, "reason": reason}

    async def _tool_list_providers(self) -> dict[str, Any]:
        return {"providers": available_provider_names()}

    # --- logistics / cargo ------------------------------------------------
    async def _tool_track_shipment(self, tracking_number: str) -> dict[str, Any]:
        shipment = await self._shipments.find_by_tracking(tracking_number)
        if shipment is None:
            raise AppError(f"No shipment matches '{tracking_number}'.")
        return self._shipment_payload(shipment, include_events=True)

    async def _tool_create_shipment(
        self,
        origin: str,
        destination: str,
        mode: str = "AIR",
        priority: int = 2,
        description: str | None = None,
        pieces: int = 1,
        weight_kg: float | None = None,
        declared_value: int = 0,
        cod_amount: int = 0,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        customer_email: str | None = None,
        requires_customs: bool = False,
        is_dangerous_goods: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        shipment = await self._shipments.create_shipment(
            origin=origin,
            destination=destination,
            mode=_parse_shipment_mode(mode),
            priority=int(priority),
            description=description,
            pieces=pieces,
            weight_kg=weight_kg,
            declared_value=declared_value,
            cod_amount=cod_amount,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            requires_customs=requires_customs,
            is_dangerous_goods=is_dangerous_goods,
            notes=notes,
            conversation_id=self._conversation_id,
            created_by_id=self._actor_id,
        )
        return self._shipment_payload(shipment, include_events=False)

    async def _tool_update_shipment_status(
        self,
        tracking_number: str,
        status: str,
        location: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        shipment = await self._shipments.find_by_tracking(tracking_number)
        if shipment is None:
            raise AppError(f"No shipment matches '{tracking_number}'.")
        new_status = _parse_shipment_status(status)
        await self._shipments.update_status(
            shipment,
            new_status,
            actor="agent",
            actor_id=self._actor_id,
            location=location,
            note=note,
        )
        return self._shipment_payload(shipment, include_events=False)

    def _shipment_payload(self, shipment: Any, *, include_events: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reference": shipment.reference,
            "tracking_number": shipment.tracking_number,
            "waybill_number": shipment.waybill_number,
            "status": shipment.status.value,
            "mode": shipment.mode.value,
            "origin": shipment.origin,
            "destination": shipment.destination,
            "pieces": shipment.pieces,
            "weight_kg": shipment.weight_kg,
            "eta_at": shipment.eta_at.isoformat() if shipment.eta_at else None,
            "delivered_at": (shipment.delivered_at.isoformat() if shipment.delivered_at else None),
            "requires_customs": shipment.requires_customs,
            "is_dangerous_goods": shipment.is_dangerous_goods,
        }
        if include_events:
            payload["events"] = [
                {
                    "event_type": event.event_type,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "location": event.location,
                    "note": event.note,
                    "occurred_at": event.occurred_at.isoformat(),
                }
                for event in shipment.events
            ]
            payload["allowed_next"] = self._shipments.expected_next_statuses(shipment)
            payload["is_overdue"] = self._shipments.sla_breaches(shipment)
        return payload
