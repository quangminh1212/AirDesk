"""Booking service: persistence, reference generation and lifecycle.

The heavy lifting of talking to providers lives in ``app.providers``; this
service turns provider results into durable, auditable bookings and enforces
the money-limit guardrail that routes expensive bookings to human approval.

The flow copies what the established products (Amadeus, Duffel, Norba) do:

1. **Search** returns *shopped* offers (reference only, not bookable).
2. **Price** turns a shopped offer into a *priced* offer with a price guarantee.
3. **Order** is only accepted for a *priced*, still-valid offer — the shopped
   offer id is refused, exactly as NDC ``OrderCreate`` requires.
4. Every step is written to an append-only ``order_events`` timeline, and order
   creation is protected by an **idempotency key** so a retried POST can never
   double-book.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.agent import Escalation, EscalationStatus
from app.models.booking import (
    Booking,
    BookingStatus,
    CabinClass,
    OrderEventRecord,
    Passenger,
    PassengerType,
    PaymentStatus,
    Ticket,
    TicketStatus,
)
from app.providers import base_types
from app.providers.registry import create_order
from app.schemas.flight import PassengerSchema
from app.services.audit import record_audit

logger = get_logger(__name__)

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: How long an unpaid hold survives before the carrier releases it.
DEFAULT_HOLD_TTL = timedelta(minutes=30)
#: Default agency markup when a provider does not set its own.
DEFAULT_MARKUP_BPS = 0  # basis points; 0 = pass-through pricing

#: Ticket statuses we recognise. Provider vocabularies outside this set are
#: preserved in the ticket's ``notes`` instead of being coerced into a value.
_KNOWN_TICKET_STATUSES = frozenset(member.value for member in TicketStatus)


def generate_reference() -> str:
    """Human-friendly booking reference, e.g. 'AD-7K2M9Q'."""
    return "AD-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


class BookingService:
    """Coordinates booking creation with guardrails and full audit."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._settings = get_settings()

    async def _unique_reference(self) -> str:
        for _ in range(5):
            ref = generate_reference()
            exists = await self._db.scalar(select(func.count()).where(Booking.reference == ref))
            if not exists:
                return ref
        raise ConflictError("Could not allocate a unique booking reference.")

    async def _find_by_idempotency_key(self, key: str) -> Booking | None:
        return await self._db.scalar(select(Booking).where(Booking.idempotency_key == key))

    def _add_event(
        self,
        booking: Booking,
        event_type: str,
        *,
        actor: str = "system",
        from_status: str | None = None,
        to_status: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Append one entry to the booking's immutable order timeline."""
        booking.events.append(
            OrderEventRecord(
                event_type=event_type,
                occurred_at=datetime.now(UTC),
                actor=actor,
                from_status=from_status,
                to_status=to_status,
                payload=payload or {},
            )
        )

    @staticmethod
    def _line_amounts(
        booking: Booking, order: base_types.OrderResult, index: int
    ) -> tuple[int, int]:
        """Split the order total across its tickets, taxes first.

        Summing the per-ticket columns must reproduce the order total, otherwise
        the ticket table would quietly disagree with the booking it belongs to.
        Integer division is safe here because VND has no minor unit.
        """
        count = max(len(order.tickets or []), 1)
        fare_total = int(booking.base_amount or 0) or int(booking.total_amount or 0)
        tax_total = int(booking.taxes_amount or 0)
        fare, tax = fare_total // count, tax_total // count
        if index == count - 1:
            # Give the remainder to the last ticket so the parts add up exactly.
            fare += fare_total - fare * count
            tax += tax_total - tax * count
        return fare, tax

    def _apply_tickets(self, booking: Booking, order: base_types.OrderResult) -> int:
        """Materialise ``order.tickets`` into ``Ticket`` rows.

        Providers are inconsistent here, which is exactly why the mapping lives
        in one place. ``mock``/``amadeus`` carry ``number`` + ``status``,
        ``duffel`` carries ``passenger``/``document_type``/``number``, and
        ``opensky`` returns a **simulated** order whose "tickets" are notes with
        no document number at all.

        Returns the number of tickets that carry a real document number. That
        count — not the mere presence of ticket entries — decides whether the
        booking may advance to ``TICKETED``. Claiming a ticket that does not
        exist is the single most damaging thing a ticketing agent can do, so a
        simulated order must never flip the status.
        """
        fallback_flight = self._first_flight_number(booking)
        issued = 0
        for index, item in enumerate(order.tickets or []):
            number = str(item.get("number") or "").strip()
            if number:
                issued += 1
            raw_status = str(item.get("status") or "").strip().lower()
            status = TicketStatus.ISSUED
            if raw_status:
                try:
                    status = TicketStatus(raw_status)
                except ValueError:
                    # Unknown provider vocabulary: keep the ticket and record
                    # the original string in notes instead of inventing a value.
                    status = TicketStatus.ISSUED
            fare, tax = self._line_amounts(booking, order, index)
            booking.tickets.append(
                Ticket(
                    ticket_number=number,
                    segment_index=int(item.get("segment_index") or index),
                    flight_number=str(item.get("flight") or fallback_flight),
                    status=status,
                    pnr=order.booking_reference,
                    fare_amount=int(item.get("fare_amount") or fare),
                    tax_amount=int(item.get("tax_amount") or tax),
                    issued_at=datetime.now(UTC) if number else None,
                    notes=self._ticket_notes(item, raw_status),
                )
            )
        return issued

    @staticmethod
    def _ticket_notes(item: dict, raw_status: str) -> str | None:
        """Preserve provider detail that a plain column cannot hold."""
        notes: list[str] = []
        if not item.get("number"):
            notes.append("No ticket number issued by provider (simulated or pending issuance).")
        if raw_status and raw_status not in _KNOWN_TICKET_STATUSES:
            notes.append(f"Provider status '{raw_status}' is outside the known set.")
        if item.get("note"):
            notes.append(str(item["note"]))
        return " ".join(notes) or None

    @staticmethod
    def _first_flight_number(booking: Booking) -> str:
        """Best-effort flight number for a ticket that names no segment."""
        snapshot = booking.offer_snapshot or {}
        for segment in snapshot.get("segments") or []:
            number = segment.get("flight_number")
            if number:
                return str(number)
        return ""

    def _replay_provider_history(self, booking: Booking, order: base_types.OrderResult) -> None:
        """Copy the provider's own order history onto our timeline.

        The timeline is append-only and is our audit trail, so provider events
        are replayed verbatim (actor, from/to status, payload) instead of being
        summarised into a single row.
        """
        for event in order.history or []:
            self._add_event(
                booking,
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type),
                actor=event.actor or "provider",
                from_status=event.from_status,
                to_status=event.to_status,
                payload=event.payload or {},
            )

    async def _reload(self, booking_id: str) -> Booking:
        """Re-fetch the booking with all relationships eagerly loaded.

        Async SQLAlchemy cannot lazily touch a relationship after ``commit()``
        (MissingGreenlet), so every returned booking is built by an explicit
        eager query rather than by ``refresh()``.
        """
        loaded = await self._db.scalar(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(
                selectinload(Booking.passengers),
                selectinload(Booking.tickets),
                selectinload(Booking.events),
            )
        )
        if loaded is None:  # pragma: no cover - the row was just written
            raise NotFoundError("Booking not found.")
        return loaded

    async def create_booking(
        self,
        *,
        offer: base_types.FlightOffer,
        passengers: list[PassengerSchema],
        contact_email: str,
        contact_phone: str | None,
        created_by_id: str | None = None,
        conversation_id: str | None = None,
        cabin: CabinClass = CabinClass.ECONOMY,
        actor_type: str = "user",
        auto_execute: bool = True,
        idempotency_key: str | None = None,
    ) -> Booking:
        """Persist a booking from a *priced* offer.

        Enforces the NDC/Duffel rule that only a priced, unexpired offer may be
        ordered. If the total exceeds the auto-approval limit, the booking is
        created in PENDING_APPROVAL and an escalation is raised instead of
        ordering.
        """
        if not offer.segments:
            raise ValidationError("Offer has no segments.")
        if not passengers:
            raise ValidationError("At least one passenger is required.")

        # Idempotency: a retried create returns the original booking untouched.
        if idempotency_key:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info("booking_idempotent_replay", reference=existing.reference)
                return existing

        # Only a priced offer may be ordered (shopped ids are refused).
        if not offer.is_bookable:
            raise ValidationError(
                "This offer has not been priced and cannot be booked. "
                "Call /flights/price first (NDC OfferPrice).",
                details={"offer_kind": offer.kind.value, "offer_id": offer.offer_id},
            )
        if not offer.price_guarantee_valid():
            raise ConflictError("The offer price guarantee has expired; please re-price the offer.")

        reference = await self._unique_reference()
        booking = Booking(
            reference=reference,
            provider=offer.provider,
            provider_offer_id=offer.offer_id,
            status=BookingStatus.DRAFT,
            cabin_class=cabin,
            origin=offer.origin,
            destination=offer.destination,
            departure_at=offer.departure_at or datetime.now(UTC),
            return_at=offer.return_at,
            airline_code=offer.primary_carrier,
            airline_name=offer.primary_carrier_name,
            currency=offer.currency,
            total_amount=offer.total_amount,
            base_amount=offer.base_amount,
            taxes_amount=offer.taxes_amount,
            markup_amount=offer.markup_amount,
            idempotency_key=idempotency_key,
            payment_status=PaymentStatus.PENDING,
            created_by_id=created_by_id,
            conversation_id=conversation_id,
            contact_email=contact_email,
            contact_phone=contact_phone,
            offer_snapshot=offer.raw,
            expires_at=datetime.now(UTC) + DEFAULT_HOLD_TTL,
            # Initialise the collections explicitly. A relationship that is
            # never touched stays absent from the instance dict, and the first
            # ``append`` then triggers a *lazy load* — which cannot run inside
            # the sync flush context of an async session (MissingGreenlet).
            # ``passengers``/``events`` happened to be appended to first;
            # ``tickets`` was not, and blew up the moment it was.
            passengers=[],
            tickets=[],
            events=[],
        )
        for p in passengers:
            booking.passengers.append(
                Passenger(
                    passenger_type=PassengerType(p.passenger_type),
                    first_name=p.first_name,
                    last_name=p.last_name,
                    date_of_birth=_parse_dt(p.date_of_birth),
                    gender=p.gender,
                    nationality=p.nationality,
                    document_type=p.document_type,
                    document_number=p.document_number,
                    document_expiry=_parse_dt(p.document_expiry),
                    email=p.email or contact_email,
                    phone=p.phone or contact_phone,
                )
            )
        self._add_event(booking, base_types.OrderEventType.CREATED.value, actor=actor_type)
        self._db.add(booking)
        await self._db.flush()

        # Guardrail: expensive bookings require human approval.
        over_limit = booking.total_amount > self._settings.booking_auto_approve_limit_vnd
        if over_limit or not auto_execute:
            booking.status = BookingStatus.PENDING_APPROVAL
            self._add_event(
                booking,
                base_types.OrderEventType.STATUS_CHANGED.value,
                actor="system",
                from_status=BookingStatus.DRAFT.value,
                to_status=BookingStatus.PENDING_APPROVAL.value,
                payload={"reason": "over_limit" if over_limit else "manual_review"},
            )
            self._db.add(
                Escalation(
                    booking_id=booking.id,
                    conversation_id=conversation_id,
                    reason="booking_over_auto_approve_limit" if over_limit else "manual_review",
                    details={
                        "total_amount": booking.total_amount,
                        "currency": booking.currency,
                        "limit": self._settings.booking_auto_approve_limit_vnd,
                    },
                    priority=1 if over_limit else 2,
                    status=EscalationStatus.OPEN,
                )
            )
            await record_audit(
                self._db,
                action="booking.pending_approval",
                entity_type="booking",
                entity_id=booking.id,
                actor_id=created_by_id,
                actor_type=actor_type,
                after={"reference": reference, "total_amount": booking.total_amount},
            )
            await self._db.commit()
            logger.info("booking_pending_approval", reference=reference)
            return booking

        # Within limit: confirm with the provider immediately.
        priced = await price_or_self(offer)
        if priced.kind is base_types.OfferKind.PRICED and priced.offer_id != offer.offer_id:
            self._add_event(
                booking,
                base_types.OrderEventType.REPRICED.value,
                actor="system",
                payload={"from": offer.offer_id, "to": priced.offer_id},
            )
        order = await create_order(
            priced,
            [self._to_pax_info(p) for p in passengers],
            contact_email=contact_email,
            contact_phone=contact_phone,
        )
        booking.status = BookingStatus.CONFIRMED
        booking.provider_order_id = order.order_id
        booking.pnr = order.booking_reference
        booking.total_amount = order.total_amount or booking.total_amount
        booking.confirmed_at = datetime.now(UTC)
        self._add_event(
            booking,
            base_types.OrderEventType.COMMITTED.value,
            actor="system",
            from_status=BookingStatus.DRAFT.value,
            to_status=BookingStatus.CONFIRMED.value,
            payload={"order_id": order.order_id, "pnr": order.booking_reference},
        )

        # Materialise the tickets the provider returned, then replay the
        # provider's own history onto our append-only timeline.
        issued = self._apply_tickets(booking, order)
        self._replay_provider_history(booking, order)

        if issued:
            # Only a real document number may promote the booking. A simulated
            # order (OpenSky) returns note-only "tickets", and telling a
            # customer they are ticketed when they are not is the failure mode
            # app/agents/qa.py exists to catch.
            booking.status = BookingStatus.TICKETED
            booking.ticketed_at = datetime.now(UTC)
            booking.payment_status = PaymentStatus.PAID
            self._add_event(
                booking,
                base_types.OrderEventType.STATUS_CHANGED.value,
                actor="system",
                from_status=BookingStatus.CONFIRMED.value,
                to_status=BookingStatus.TICKETED.value,
                payload={"tickets": issued},
            )
        else:
            booking.payment_status = PaymentStatus.PENDING
            logger.warning(
                "booking_confirmed_without_tickets",
                reference=reference,
                provider=order.provider,
                order_status=order.order_status,
            )

        await record_audit(
            self._db,
            action="booking.confirmed",
            entity_type="booking",
            entity_id=booking.id,
            actor_id=created_by_id,
            actor_type=actor_type,
            after={
                "reference": reference,
                "pnr": booking.pnr,
                "order_id": order.order_id,
                "status": booking.status.value,
                "tickets_issued": issued,
            },
        )
        await self._db.commit()
        logger.info("booking_confirmed", reference=reference, pnr=booking.pnr, tickets=issued)
        return await self._reload(booking.id)

    async def get(self, booking_id: str) -> Booking:
        booking = await self._db.scalar(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(
                selectinload(Booking.passengers),
                selectinload(Booking.tickets),
                selectinload(Booking.events),
            )
        )
        if booking is None:
            raise NotFoundError("Booking not found.")
        return booking

    async def get_by_reference(self, reference: str) -> Booking:
        booking = await self._db.scalar(
            select(Booking)
            .where(Booking.reference == reference)
            .options(
                selectinload(Booking.passengers),
                selectinload(Booking.tickets),
                selectinload(Booking.events),
            )
        )
        if booking is None:
            raise NotFoundError("Booking not found.")
        return booking

    async def list_bookings(
        self, *, status: BookingStatus | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Booking], int]:
        stmt = select(Booking).order_by(Booking.created_at.desc())
        count_stmt = select(func.count()).select_from(Booking)
        if status:
            stmt = stmt.where(Booking.status == status)
            count_stmt = count_stmt.where(Booking.status == status)
        total = await self._db.scalar(count_stmt) or 0
        rows = (await self._db.scalars(stmt.limit(limit).offset(offset))).all()
        return list(rows), total

    async def cancel(self, booking_id: str, *, actor_id: str | None = None) -> Booking:
        booking = await self.get(booking_id)
        if booking.status in (BookingStatus.CANCELLED, BookingStatus.FAILED):
            raise ConflictError("Booking is already cancelled or failed.")
        previous = booking.status.value
        booking.status = BookingStatus.CANCELLED
        self._add_event(
            booking,
            base_types.OrderEventType.CANCELLED.value,
            actor="user" if actor_id else "system",
            from_status=previous,
            to_status=BookingStatus.CANCELLED.value,
        )
        await record_audit(
            self._db,
            action="booking.cancelled",
            entity_type="booking",
            entity_id=booking.id,
            actor_id=actor_id,
            before={"status": previous},
            after={"status": "cancelled"},
        )
        await self._db.commit()
        return booking

    @staticmethod
    def _to_pax_info(p: PassengerSchema) -> base_types.PassengerInfo:
        return base_types.PassengerInfo(
            passenger_type=p.passenger_type,
            first_name=p.first_name,
            last_name=p.last_name,
            date_of_birth=p.date_of_birth,
            gender=p.gender,
            nationality=p.nationality,
            document_type=p.document_type,
            document_number=p.document_number,
            document_expiry=p.document_expiry,
            email=p.email,
            phone=p.phone,
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def price_or_self(offer: base_types.FlightOffer) -> base_types.FlightOffer:
    """Re-price an offer, tolerating providers that price on the fly."""
    from app.providers.registry import price_offer

    try:
        return await price_offer(offer)
    except Exception as exc:  # noqa: BLE001 - keep the original offer if repricing fails
        logger.warning("price_refresh_failed", provider=offer.provider, error=str(exc))
        return offer
