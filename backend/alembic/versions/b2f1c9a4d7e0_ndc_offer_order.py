"""ndc offer/order: order_events, markup, idempotency, hold expiry

Adds the pieces that turn AirDesk's booking flow into the standard NDC /
Duffel / Norba offer->order model:

* ``bookings.markup_amount`` — agency fee included in the total.
* ``bookings.idempotency_key`` — de-dupes retried ``POST /bookings``.
* ``bookings.expires_at`` — deadline for an unpaid hold.
* ``order_events`` — append-only order timeline (who did what, when).

Revision ID: b2f1c9a4d7e0
Revises: 70509d7f62fc
Create Date: 2026-10-04 23:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2f1c9a4d7e0"
down_revision: str | None = "70509d7f62fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("markup_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "bookings",
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_bookings_idempotency_key", "bookings", ["idempotency_key"], unique=True
    )

    op.create_table(
        "order_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("booking_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_events_booking_id", "order_events", ["booking_id"])
    op.create_index(
        "ix_order_events_booking_time", "order_events", ["booking_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_order_events_booking_time", table_name="order_events")
    op.drop_index("ix_order_events_booking_id", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_bookings_idempotency_key", table_name="bookings")
    op.drop_column("bookings", "expires_at")
    op.drop_column("bookings", "idempotency_key")
    op.drop_column("bookings", "markup_amount")
