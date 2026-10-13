"""Logistics endpoints: shipments, tracking timeline and carrier hand-off.

Backs the operations side of the agency — the customer asks "hàng của tôi đang
ở đâu?" and the operator (or the agent) answers from one timeline instead of
phoning the forwarder.

Design mirrors ``tasks.py``:

* every mutation is audited with before/after snapshots;
* the status machine is enforced in the service, so an out-of-order carrier
  webhook is rejected rather than corrupting the timeline;
* list endpoints are paginated with the shared ``Page`` envelope;
* ``/stats`` always returns every status/mode bucket so the dashboard has no
  holes to special-case.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, status
from sqlalchemy import ColumnElement, and_, func, select

from app.api.deps import DbSession, OperatorUser, Pagination
from app.core.errors import NotFoundError, ValidationError
from app.models.shipment import (
    ATTENTION_SHIPMENT_STATUSES,
    CLOSED_SHIPMENT_STATUSES,
    Shipment,
    ShipmentMode,
    ShipmentStatus,
)
from app.schemas.common import Page
from app.schemas.shipment import (
    ShipmentCancel,
    ShipmentCreate,
    ShipmentEventRead,
    ShipmentModeCount,
    ShipmentRead,
    ShipmentStats,
    ShipmentStatusCount,
    ShipmentStatusUpdate,
    ShipmentSummary,
    TrackingEventCreate,
)
from app.services.shipment import ShipmentService

router = APIRouter(prefix="/shipments", tags=["logistics"])

#: Current ordering contract: most urgent first, then soonest ETA, then newest.
_DEFAULT_ORDER = (
    Shipment.priority.asc(),
    Shipment.eta_at.asc(),
    Shipment.created_at.desc(),
)

_OPEN_STATUSES = tuple(s for s in ShipmentStatus if s not in CLOSED_SHIPMENT_STATUSES)

#: Statuses that mean the consignment has not been physically collected yet,
#: so carrier scans cannot be attached to them.
_PRE_PICKUP_STATUSES = (ShipmentStatus.DRAFT, ShipmentStatus.BOOKED)


def _overdue_clauses() -> list[ColumnElement[bool]]:
    """Predicates selecting "past its ETA and still open" shipments."""
    return [
        Shipment.eta_at.is_not(None),
        Shipment.eta_at < datetime.now(UTC),
        Shipment.status.not_in(CLOSED_SHIPMENT_STATUSES),
    ]


def _to_read(shipment: Shipment, *, service: ShipmentService) -> ShipmentRead:
    """Serialize a shipment, attaching the two derived display fields."""
    payload = ShipmentRead.model_validate(shipment)
    payload.is_overdue = service.sla_breaches(shipment)
    payload.allowed_next = service.expected_next_statuses(shipment)
    return payload


@router.post(
    "",
    response_model=ShipmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shipment (operator)",
)
async def create_shipment(
    db: DbSession, operator: OperatorUser, payload: ShipmentCreate
) -> ShipmentRead:
    service = ShipmentService(db)
    shipment = await service.create_shipment(
        origin=payload.origin,
        destination=payload.destination,
        mode=payload.mode,
        priority=payload.priority,
        carrier=payload.carrier,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_email=payload.customer_email,
        description=payload.description,
        pieces=payload.pieces,
        weight_kg=payload.weight_kg,
        declared_value=payload.declared_value,
        cod_amount=payload.cod_amount,
        currency=payload.currency,
        requires_customs=payload.requires_customs,
        is_dangerous_goods=payload.is_dangerous_goods,
        eta_at=payload.eta_at,
        created_by_id=operator.id,
        conversation_id=payload.conversation_id,
        booking_id=payload.booking_id,
        notes=payload.notes,
        idempotency_key=payload.idempotency_key,
        actor_type="operator",
    )
    return _to_read(shipment, service=service)


@router.get("", response_model=Page[ShipmentSummary], summary="List shipments")
async def list_shipments(
    db: DbSession,
    _user: OperatorUser,
    pagination: Pagination,
    status_filter: ShipmentStatus | None = Query(None, alias="status"),
    mode: ShipmentMode | None = Query(None),
    carrier: str | None = Query(None),
    tracking_number: str | None = Query(None),
    customer_phone: str | None = Query(None),
    overdue: bool | None = Query(
        None, description="true = only overdue open shipments, false = only non-overdue."
    ),
    needs_attention: bool | None = Query(
        None, description="true = delayed / exception / customs holds only."
    ),
) -> Page[ShipmentSummary]:
    page, page_size, offset = pagination

    filters: list[ColumnElement[bool]] = []
    if status_filter is not None:
        filters.append(Shipment.status == status_filter)
    if mode is not None:
        filters.append(Shipment.mode == mode)
    if carrier is not None:
        filters.append(Shipment.carrier == carrier)
    if tracking_number is not None:
        filters.append(Shipment.tracking_number == tracking_number)
    if customer_phone is not None:
        filters.append(Shipment.customer_phone == customer_phone)
    if needs_attention is not None:
        clause = Shipment.status.in_(ATTENTION_SHIPMENT_STATUSES)
        filters.append(clause if needs_attention else ~clause)
    if overdue is not None:
        clauses = _overdue_clauses()
        filters.append(and_(*clauses) if overdue else ~and_(*clauses))

    stmt = select(Shipment)
    count_stmt = select(func.count()).select_from(Shipment)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = await db.scalar(count_stmt) or 0
    rows = (await db.scalars(stmt.order_by(*_DEFAULT_ORDER).limit(page_size).offset(offset))).all()
    service = ShipmentService(db)
    items = []
    for row in rows:
        summary = ShipmentSummary.model_validate(row)
        summary.is_overdue = service.sla_breaches(row)
        items.append(summary)
    return Page.create(items, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=ShipmentStats, summary="Logistics statistics")
async def shipment_stats(db: DbSession, _user: OperatorUser) -> ShipmentStats:
    status_rows = (
        await db.execute(select(Shipment.status, func.count()).group_by(Shipment.status))
    ).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    mode_rows = (
        await db.execute(select(Shipment.mode, func.count()).group_by(Shipment.mode))
    ).all()
    mode_counts = {row[0]: row[1] for row in mode_rows}

    overdue = await db.scalar(select(func.count()).select_from(Shipment).where(*_overdue_clauses()))
    attention = await db.scalar(
        select(func.count())
        .select_from(Shipment)
        .where(Shipment.status.in_(ATTENTION_SHIPMENT_STATUSES))
    )
    in_transit = await db.scalar(
        select(func.count())
        .select_from(Shipment)
        .where(
            Shipment.status.in_(
                (
                    ShipmentStatus.PICKED_UP,
                    ShipmentStatus.IN_TRANSIT,
                    ShipmentStatus.OUT_FOR_DELIVERY,
                )
            )
        )
    )
    week_ago = datetime.now(UTC) - timedelta(days=7)
    delivered_week = await db.scalar(
        select(func.count())
        .select_from(Shipment)
        .where(Shipment.status == ShipmentStatus.DELIVERED, Shipment.delivered_at >= week_ago)
    )
    declared_total = await db.scalar(
        select(func.coalesce(func.sum(Shipment.declared_value), 0)).select_from(Shipment)
    )

    return ShipmentStats(
        total=sum(status_counts.values()),
        by_status=[
            ShipmentStatusCount(status=s, count=status_counts.get(s, 0)) for s in ShipmentStatus
        ],
        by_mode=[ShipmentModeCount(mode=m, count=mode_counts.get(m, 0)) for m in ShipmentMode],
        in_transit=in_transit or 0,
        needs_attention=attention or 0,
        overdue=overdue or 0,
        delivered_this_week=delivered_week or 0,
        total_declared_value_vnd=int(declared_total or 0),
    )


@router.get("/track/{tracking_number}", response_model=ShipmentRead, summary="Track by number")
async def track_shipment(db: DbSession, _user: OperatorUser, tracking_number: str) -> ShipmentRead:
    """Resolve a shipment from a carrier tracking number, waybill or reference.

    This is the endpoint the agent calls when a customer forwards a bare
    tracking code: it accepts any of the three identifiers because customers
    never know which one they are holding.
    """
    service = ShipmentService(db)
    shipment = await service.find_by_tracking(tracking_number)
    if shipment is None:
        raise NotFoundError(f"No shipment matches '{tracking_number}'.")
    return _to_read(shipment, service=service)


@router.get("/{shipment_id}", response_model=ShipmentRead, summary="Get one shipment")
async def get_shipment(db: DbSession, _user: OperatorUser, shipment_id: str) -> ShipmentRead:
    service = ShipmentService(db)
    shipment = await service.get(shipment_id)
    return _to_read(shipment, service=service)


@router.post("/{shipment_id}/status", response_model=ShipmentRead, summary="Advance the status")
async def update_shipment_status(
    db: DbSession,
    operator: OperatorUser,
    shipment_id: str,
    payload: ShipmentStatusUpdate,
) -> ShipmentRead:
    service = ShipmentService(db)
    shipment = await service.get(shipment_id)
    shipment = await service.update_status(
        shipment,
        payload.status,
        actor="operator",
        actor_id=operator.id,
        location=payload.location,
        note=payload.note,
    )
    return _to_read(shipment, service=service)


@router.post(
    "/{shipment_id}/events",
    response_model=ShipmentEventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Append a tracking scan",
)
async def add_tracking_event(
    db: DbSession,
    operator: OperatorUser,
    shipment_id: str,
    payload: TrackingEventCreate,
) -> ShipmentEventRead:
    """Record a carrier scan without forcing a status change.

    Carriers emit scans ("arrived HAN", "customs hold") that do not always map
    onto our status machine; this endpoint keeps them on the timeline verbatim.
    """
    service = ShipmentService(db)
    shipment = await service.get(shipment_id)
    # A carrier scan is evidence of physical movement, so it is meaningless
    # while the shipment is still DRAFT/BOOKED - i.e. not yet collected. The
    # guard used to look only at ``occurred_at``, which let scans through.
    if shipment.status in _PRE_PICKUP_STATUSES:
        raise ValidationError("Cannot record a scan before the shipment is picked up.")
    shipment = await service.add_tracking_event(
        shipment,
        event_type=payload.event_type,
        location=payload.location,
        note=payload.note,
        occurred_at=payload.occurred_at,
        actor="operator",
        payload=payload.payload,
    )
    newest = max(shipment.events, key=lambda e: e.created_at)
    return ShipmentEventRead.model_validate(newest)


@router.post("/{shipment_id}/cancel", response_model=ShipmentRead, summary="Cancel a shipment")
async def cancel_shipment(
    db: DbSession, operator: OperatorUser, shipment_id: str, payload: ShipmentCancel
) -> ShipmentRead:
    service = ShipmentService(db)
    shipment = await service.get(shipment_id)
    shipment = await service.cancel(shipment, reason=payload.reason, actor_id=operator.id)
    return _to_read(shipment, service=service)


__all__ = ["router"]
