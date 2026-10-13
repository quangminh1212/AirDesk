"""Shipment service: persistence, reference generation and tracking lifecycle.

This is the logistics twin of :mod:`app.services.booking`. It follows the same
three rules that make the booking service auditable and safe to retry:

1. **Idempotency** — creating a shipment twice with the same
   ``idempotency_key`` returns the original row instead of a duplicate.
2. **Append-only timeline** — every status transition writes a
   ``shipment_events`` row with actor and from/to status, so history is
   replayable.
3. **Guardrails** — transitions that need a human (delivered-without-proof,
   exception, customs hold) raise an escalation *and* a task instead of silently
   changing state.

Dangerous goods and high declared value are routed to human approval in exactly
the same spirit as the booking money-limit guardrail.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.shipment import (
    ATTENTION_SHIPMENT_STATUSES,
    CLOSED_SHIPMENT_STATUSES,
    Shipment,
    ShipmentEvent,
    ShipmentMode,
    ShipmentPriority,
    ShipmentStatus,
)
from app.models.task import CLOSED_TASK_STATUSES, Task, TaskType
from app.services.audit import record_audit

logger = get_logger(__name__)

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: Which statuses are allowed to follow which. Keeps the timeline monotone and
#: rejects nonsense like ``delivered -> booked`` while still allowing a shipment
#: to be re-queued after a delay or an exception.
ALLOWED_TRANSITIONS: dict[ShipmentStatus, frozenset[ShipmentStatus]] = {
    ShipmentStatus.DRAFT: frozenset({ShipmentStatus.BOOKED, ShipmentStatus.CANCELLED}),
    ShipmentStatus.BOOKED: frozenset(
        {ShipmentStatus.PICKED_UP, ShipmentStatus.CANCELLED, ShipmentStatus.DELAYED}
    ),
    ShipmentStatus.PICKED_UP: frozenset(
        {
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.CUSTOMS,
            ShipmentStatus.DELAYED,
            ShipmentStatus.EXCEPTION,
            ShipmentStatus.CANCELLED,
        }
    ),
    ShipmentStatus.IN_TRANSIT: frozenset(
        {
            ShipmentStatus.CUSTOMS,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELAYED,
            ShipmentStatus.EXCEPTION,
        }
    ),
    ShipmentStatus.CUSTOMS: frozenset(
        {
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELAYED,
            ShipmentStatus.EXCEPTION,
            ShipmentStatus.RETURNED,
        }
    ),
    ShipmentStatus.OUT_FOR_DELIVERY: frozenset(
        {
            ShipmentStatus.DELIVERED,
            ShipmentStatus.DELAYED,
            ShipmentStatus.EXCEPTION,
            ShipmentStatus.RETURNED,
        }
    ),
    ShipmentStatus.DELAYED: frozenset(
        {
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.CUSTOMS,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.EXCEPTION,
            ShipmentStatus.CANCELLED,
        }
    ),
    ShipmentStatus.EXCEPTION: frozenset(
        {
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.OUT_FOR_DELIVERY,
            ShipmentStatus.DELIVERED,
            ShipmentStatus.RETURNED,
            ShipmentStatus.CANCELLED,
        }
    ),
    ShipmentStatus.DELIVERED: frozenset({ShipmentStatus.RETURNED}),
    ShipmentStatus.CANCELLED: frozenset(),
    ShipmentStatus.RETURNED: frozenset(),
}

#: Declared value above which a human must approve the shipment (VND).
DEFAULT_APPROVAL_LIMIT_VND = 50_000_000


def generate_reference() -> str:
    """Human-friendly shipment reference, e.g. 'SHP-7K2M9Q'."""
    return "SHP-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


class ShipmentService:
    """Coordinates shipment creation, tracking and carrier hand-off."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._settings = get_settings()

    # --- helpers -----------------------------------------------------------
    async def _unique_reference(self) -> str:
        for _ in range(5):
            ref = generate_reference()
            exists = await self._db.scalar(select(func.count()).where(Shipment.reference == ref))
            if not exists:
                return ref
        raise ConflictError("Could not allocate a unique shipment reference.")

    def _add_event(
        self,
        shipment: Shipment,
        event_type: str,
        *,
        actor: str = "system",
        from_status: str | None = None,
        to_status: str | None = None,
        location: str | None = None,
        note: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Append one entry to the shipment's immutable tracking timeline."""
        shipment.events.append(
            ShipmentEvent(
                event_type=event_type,
                occurred_at=occurred_at or datetime.now(UTC),
                actor=actor,
                location=location,
                from_status=from_status,
                to_status=to_status,
                note=note,
                payload=payload or {},
            )
        )

    @property
    def approval_limit_vnd(self) -> int:
        """Declared-value ceiling before a human must approve."""
        return int(
            getattr(
                self._settings,
                "shipment_auto_approve_limit_vnd",
                DEFAULT_APPROVAL_LIMIT_VND,
            )
        )

    # --- queries -----------------------------------------------------------
    async def get(self, shipment_id: str) -> Shipment:
        shipment = await self._db.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .options(selectinload(Shipment.events))
        )
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        return shipment

    async def find_by_tracking(self, tracking_number: str) -> Shipment | None:
        """Look a shipment up by any of the carrier-issued identifiers."""
        return await self._db.scalar(
            select(Shipment)
            .where(
                or_(
                    Shipment.tracking_number == tracking_number,
                    Shipment.waybill_number == tracking_number,
                    Shipment.reference == tracking_number,
                )
            )
            .options(selectinload(Shipment.events))
        )

    # --- writes ------------------------------------------------------------
    async def create_shipment(
        self,
        *,
        origin: str,
        destination: str,
        mode: ShipmentMode = ShipmentMode.AIR,
        priority: int = ShipmentPriority.NORMAL.value,
        carrier: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        customer_email: str | None = None,
        description: str | None = None,
        pieces: int = 1,
        weight_kg: float | None = None,
        declared_value: int = 0,
        cod_amount: int = 0,
        currency: str = "VND",
        requires_customs: bool = False,
        is_dangerous_goods: bool = False,
        eta_at: datetime | None = None,
        created_by_id: str | None = None,
        conversation_id: str | None = None,
        booking_id: str | None = None,
        notes: str | None = None,
        idempotency_key: str | None = None,
        actor_type: str = "user",
    ) -> Shipment:
        """Create a shipment, returning the existing row on a retried request.

        New shipments start in ``DRAFT``. If the declared value exceeds the
        approval ceiling, or the goods are flagged as dangerous, an approval
        task is raised for a human instead of letting the agent hand the goods
        to a carrier unattended.
        """
        if not origin.strip() or not destination.strip():
            raise ValidationError("Both origin and destination are required.")
        if pieces < 1:
            raise ValidationError("A shipment must have at least one piece.")

        if idempotency_key:
            existing = await self._db.scalar(
                select(Shipment)
                .where(Shipment.idempotency_key == idempotency_key)
                .options(selectinload(Shipment.events))
            )
            if existing is not None:
                logger.info("shipment_idempotent_hit", reference=existing.reference)
                return existing

        shipment = Shipment(
            reference=await self._unique_reference(),
            status=ShipmentStatus.DRAFT,
            mode=mode,
            priority=priority,
            carrier=carrier,
            origin=origin.strip(),
            destination=destination.strip(),
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            description=description,
            pieces=pieces,
            weight_kg=weight_kg,
            declared_value=declared_value,
            cod_amount=cod_amount,
            currency=currency.upper()[:3],
            requires_customs=requires_customs,
            is_dangerous_goods=is_dangerous_goods,
            eta_at=eta_at,
            created_by_id=created_by_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            notes=notes,
            idempotency_key=idempotency_key,
            meta={},
        )
        self._add_event(
            shipment,
            "created",
            actor=actor_type,
            to_status=ShipmentStatus.DRAFT.value,
            note="Shipment created.",
        )

        needs_approval = is_dangerous_goods or declared_value > self.approval_limit_vnd
        if needs_approval:
            reason = "dangerous goods" if is_dangerous_goods else f"declared value {declared_value}"
            await self._raise_attention(
                shipment,
                task_type=TaskType.LOGISTICS,
                title=f"Duyệt lô hàng {shipment.reference} ({reason})",
                description=(
                    f"Lô hàng {shipment.reference} {origin} -> {destination} cần người duyệt "
                    f"trước khi giao cho hãng vận chuyển. Lý do: {reason}."
                ),
                priority=ShipmentPriority.URGENT.value,
                actor_type=actor_type,
            )
            shipment.meta = {**(shipment.meta or {}), "requires_approval": True}

        self._db.add(shipment)
        await self._db.flush()
        await record_audit(
            self._db,
            action="shipment.created",
            entity_type="shipment",
            entity_id=shipment.id,
            actor_id=created_by_id,
            actor_type=actor_type,
            after={"reference": shipment.reference, "status": shipment.status.value},
        )
        await self._db.commit()
        await self._db.refresh(shipment, attribute_names=["events"])
        logger.info(
            "shipment_created",
            reference=shipment.reference,
            mode=shipment.mode.value,
            needs_approval=needs_approval,
        )
        return shipment

    async def update_status(
        self,
        shipment: Shipment,
        new_status: ShipmentStatus,
        *,
        actor: str = "operator",
        actor_id: str | None = None,
        location: str | None = None,
        note: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Shipment:
        """Move a shipment to ``new_status``, appending a timeline entry.

        Raises :class:`ValidationError` for a transition the lifecycle forbids,
        so a mis-parsed carrier webhook cannot silently rewind a delivered
        shipment.
        """
        if new_status is shipment.status:
            return shipment

        allowed = ALLOWED_TRANSITIONS.get(shipment.status, frozenset())
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot move shipment from '{shipment.status.value}' to '{new_status.value}'."
            )

        previous = shipment.status
        shipment.status = new_status
        now = datetime.now(UTC)

        if new_status is ShipmentStatus.PICKED_UP and shipment.picked_up_at is None:
            shipment.picked_up_at = now
        if new_status is ShipmentStatus.DELIVERED:
            shipment.delivered_at = now

        self._add_event(
            shipment,
            f"status:{new_status.value}",
            actor=actor,
            from_status=previous.value,
            to_status=new_status.value,
            location=location,
            note=note,
            payload=payload,
            occurred_at=now,
        )

        # Anything that means "a human must act" gets a task, and the two truly
        # bad states also get an escalation so the on-call sees them.
        if new_status in ATTENTION_SHIPMENT_STATUSES:
            await self._raise_attention(
                shipment,
                task_type=TaskType.LOGISTICS,
                title=f"Xử lý lô hàng {shipment.reference} ({new_status.value})",
                description=(
                    f"Lô hàng {shipment.reference} {shipment.origin} -> {shipment.destination} "
                    f"chuyển sang trạng thái {new_status.value}."
                    + (f" Ghi chú: {note}" if note else "")
                ),
                priority=(
                    ShipmentPriority.URGENT.value
                    if new_status in (ShipmentStatus.EXCEPTION, ShipmentStatus.CUSTOMS)
                    else ShipmentPriority.NORMAL.value
                ),
                actor_type=actor,
            )

        await record_audit(
            self._db,
            action="shipment.status_changed",
            entity_type="shipment",
            entity_id=shipment.id,
            actor_id=actor_id,
            actor_type="user",
            before={"status": previous.value},
            after={"status": new_status.value},
        )
        await self._db.commit()
        await self._db.refresh(shipment, attribute_names=["events"])
        logger.info(
            "shipment_status_changed",
            reference=shipment.reference,
            from_status=previous.value,
            to_status=new_status.value,
        )
        return shipment

    async def add_tracking_event(
        self,
        shipment: Shipment,
        *,
        event_type: str,
        location: str | None = None,
        note: str | None = None,
        occurred_at: datetime | None = None,
        actor: str = "carrier",
        payload: dict[str, Any] | None = None,
    ) -> Shipment:
        """Append a carrier-reported scan without changing the shipment status."""
        self._add_event(
            shipment,
            event_type,
            actor=actor,
            location=location,
            note=note,
            payload=payload,
            occurred_at=occurred_at,
        )
        await self._db.commit()
        await self._db.refresh(shipment, attribute_names=["events"])
        return shipment

    async def cancel(
        self, shipment: Shipment, *, reason: str, actor_id: str | None = None
    ) -> Shipment:
        """Cancel a shipment that has not been delivered yet."""
        if shipment.status in CLOSED_SHIPMENT_STATUSES:
            raise ConflictError(
                f"Shipment {shipment.reference} is already {shipment.status.value}."
            )
        return await self.update_status(
            shipment,
            ShipmentStatus.CANCELLED,
            actor="operator",
            actor_id=actor_id,
            note=reason,
        )

    # --- guardrails --------------------------------------------------------
    async def _raise_attention(
        self,
        shipment: Shipment,
        *,
        task_type: TaskType,
        title: str,
        description: str,
        priority: int,
        actor_type: str,
    ) -> Task:
        """Create the operator task that carries a shipment needing a human."""
        task = Task(
            title=title,
            description=description,
            priority=priority,
            task_type=task_type,
            conversation_id=shipment.conversation_id,
            booking_id=shipment.booking_id,
            meta={"shipment_id": shipment.reference, "shipment_status": shipment.status.value},
        )
        self._db.add(task)
        await self._db.flush()
        logger.info(
            "shipment_task_raised",
            reference=shipment.reference,
            task_id=task.id,
            priority=priority,
        )
        return task

    def sla_breaches(self, shipment: Shipment, *, now: datetime | None = None) -> bool:
        """True when an open shipment is past its promised ETA."""
        if shipment.status in CLOSED_SHIPMENT_STATUSES or shipment.eta_at is None:
            return False
        reference = now or datetime.now(UTC)
        eta = shipment.eta_at
        if eta.tzinfo is None:
            eta = eta.replace(tzinfo=UTC)
        return reference > eta

    def expected_next_statuses(self, shipment: Shipment) -> list[str]:
        """Statuses an operator/agent may legally move this shipment to."""
        return sorted(s.value for s in ALLOWED_TRANSITIONS.get(shipment.status, frozenset()))

    async def open_tasks_for(self, shipment: Shipment) -> list[Task]:
        """Open logistics tasks raised for this shipment.

        Guardrails (dangerous goods, high declared value, DELAYED/EXCEPTION/
        CUSTOMS) each open a task instead of silently proceeding. The API and
        the agent both need to answer "is a human already on this?", so the
        lookup lives here rather than being rebuilt per caller.
        """
        reference = shipment.reference
        result = await self._db.execute(
            select(Task)
            .where(
                Task.task_type == TaskType.LOGISTICS,
                Task.status.notin_(CLOSED_TASK_STATUSES),
            )
            .order_by(Task.priority.asc(), Task.created_at.asc())
        )
        # The reference lives in ``meta`` (JSON), whose querying differs between
        # Postgres and SQLite, so filter in Python to keep both dialects equal.
        return [
            task
            for task in result.scalars().all()
            if isinstance(task.meta, dict) and task.meta.get("shipment_id") == reference
        ]


def default_eta(mode: ShipmentMode, *, now: datetime | None = None) -> datetime:
    """Rough promised-ETA default per transport mode (operator can override)."""
    reference = now or datetime.now(UTC)
    hours = {
        ShipmentMode.AIR: 24,
        ShipmentMode.COURIER: 48,
        ShipmentMode.ROAD: 72,
        ShipmentMode.RAIL: 96,
        ShipmentMode.SEA: 24 * 14,
    }.get(mode, 72)
    return reference + timedelta(hours=hours)
