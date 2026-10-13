"""Booking endpoints: create, list, get, cancel."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, status

from app.api.deps import CurrentUserDep, DbSession, Pagination
from app.core.errors import NotFoundError
from app.models.booking import Booking, BookingStatus, CabinClass
from app.providers.codec import offer_from_dict
from app.schemas.booking import (
    BookingDetail,
    BookingRead,
    OrderEventRead,
    PassengerRead,
    TicketRead,
)
from app.schemas.common import Page
from app.schemas.flight import BookingCreateRequest
from app.services.booking import BookingService
from app.services.offer_cache import get_cached_offer

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _to_detail(booking: Booking) -> BookingDetail:
    detail = BookingDetail.model_validate(booking)
    detail.passengers = [PassengerRead.model_validate(p) for p in booking.passengers]
    detail.events = [OrderEventRead.model_validate(e) for e in getattr(booking, "events", [])]
    detail.tickets = [TicketRead.model_validate(t) for t in getattr(booking, "tickets", [])]
    return detail


@router.post(
    "",
    response_model=BookingDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an order from a priced offer",
)
async def create_booking(
    db: DbSession,
    user: CurrentUserDep,
    payload: BookingCreateRequest,
    idempotency_key: str | None = Header(
        None,
        alias="Idempotency-Key",
        description=(
            "Optional. A retried POST with the same key returns the original "
            "order instead of creating a duplicate (NDC/Duffel requirement)."
        ),
    ),
) -> BookingDetail:
    cached = await get_cached_offer(payload.provider, payload.offer_id)
    if cached is None:
        raise NotFoundError("Offer not found or expired. Please search again.")
    offer = offer_from_dict(cached)

    service = BookingService(db)
    booking = await service.create_booking(
        offer=offer,
        passengers=payload.passengers,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        created_by_id=user.id,
        # `payload.cabin` is the provider-layer enum; the service persists the
        # model-layer one. They share values today, but converting explicitly
        # keeps the layers decoupled instead of relying on that coincidence.
        cabin=CabinClass(payload.cabin.value),
        actor_type="user",
        idempotency_key=idempotency_key,
    )
    # Reload with passengers + events + tickets eagerly for the response.
    loaded = await service.get(booking.id)
    return _to_detail(loaded)


@router.get("", response_model=Page[BookingRead], summary="List bookings")
async def list_bookings(
    db: DbSession,
    _user: CurrentUserDep,
    pagination: Pagination,
    status_filter: BookingStatus | None = Query(None, alias="status"),
) -> Page[BookingRead]:
    page, page_size, offset = pagination
    service = BookingService(db)
    rows, total = await service.list_bookings(status=status_filter, limit=page_size, offset=offset)
    items = [BookingRead.model_validate(b) for b in rows]
    return Page.create(items, total=total, page=page, page_size=page_size)


@router.get("/{booking_id}", response_model=BookingDetail, summary="Get booking detail")
async def get_booking(db: DbSession, _user: CurrentUserDep, booking_id: str) -> BookingDetail:
    service = BookingService(db)
    booking = await service.get(booking_id)
    return _to_detail(booking)


@router.get(
    "/by-reference/{reference}",
    response_model=BookingDetail,
    summary="Get booking by reference",
)
async def get_by_reference(db: DbSession, _user: CurrentUserDep, reference: str) -> BookingDetail:
    service = BookingService(db)
    booking = await service.get_by_reference(reference)
    return _to_detail(booking)


@router.post("/{booking_id}/cancel", response_model=BookingDetail, summary="Cancel a booking")
async def cancel_booking(db: DbSession, user: CurrentUserDep, booking_id: str) -> BookingDetail:
    service = BookingService(db)
    await service.cancel(booking_id, actor_id=user.id)
    booking = await service.get(booking_id)
    return _to_detail(booking)
