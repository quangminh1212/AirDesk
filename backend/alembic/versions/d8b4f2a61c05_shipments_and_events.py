"""shipments and shipment events

Adds the logistics side of the desk: the consignments the agency moves on
behalf of customers (cargo, documents, oversize baggage) and their append-only
tracking timeline.

* ``shipments`` — one row per consignment (``app.models.shipment``).
* ``shipment_events`` — the immutable scan/transition trail. Nothing ever
  updates an event, so the timeline can be replayed to answer "where is it?".

Design notes:

* Every enum is a ``VARCHAR`` (``native_enum=False``) with the exact ``length``
  from the model, so Postgres and SQLite generate the same schema.
* Money is ``BIGINT`` minor units (VND has no decimals, so 1 VND == 1 unit).
  ``weight_kg`` is ``FLOAT`` on purpose — it is a measurement, not money, and
  ``Numeric`` would diverge between the two backends.
* ``ix_shipments_idempotency_key`` is UNIQUE and nullable: PostgreSQL and SQLite
  both allow many NULLs, so only real retries collide.

Revision ID: d8b4f2a61c05
Revises: c3a7e1d94b62
Create Date: 2026-10-14 10:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8b4f2a61c05"
down_revision: str | None = "c3a7e1d94b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SHIPMENT_STATUSES = (
    "DRAFT",
    "BOOKED",
    "PICKED_UP",
    "IN_TRANSIT",
    "CUSTOMS",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "DELAYED",
    "EXCEPTION",
    "CANCELLED",
    "RETURNED",
)

_SHIPMENT_MODES = ("AIR", "SEA", "ROAD", "RAIL", "COURIER")


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("reference", sa.String(length=24), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_SHIPMENT_STATUSES, name="shipmentstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum(*_SHIPMENT_MODES, name="shipmentmode", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("carrier", sa.String(length=120), nullable=True),
        sa.Column("tracking_number", sa.String(length=80), nullable=True),
        sa.Column("waybill_number", sa.String(length=80), nullable=True),
        sa.Column("origin", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("destination", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("customer_name", sa.String(length=160), nullable=True),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("pieces", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("declared_value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cod_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="VND"),
        sa.Column(
            "requires_customs", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_dangerous_goods", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("booking_id", sa.String(length=36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shipments_id"), "shipments", ["id"], unique=False)
    op.create_index(
        "ix_shipments_conversation_id", "shipments", ["conversation_id"], unique=False
    )
    op.create_index("ix_shipments_reference", "shipments", ["reference"], unique=True)
    op.create_index(
        "ix_shipments_idempotency_key", "shipments", ["idempotency_key"], unique=True
    )
    op.create_index("ix_shipments_tracking", "shipments", ["tracking_number"], unique=False)
    op.create_index("ix_shipments_status_eta", "shipments", ["status", "eta_at"], unique=False)

    op.create_table(
        "shipment_events",
        sa.Column("shipment_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("location", sa.String(length=160), nullable=True),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shipment_events_id"), "shipment_events", ["id"], unique=False)
    op.create_index(
        "ix_shipment_events_shipment_id", "shipment_events", ["shipment_id"], unique=False
    )
    op.create_index(
        "ix_shipment_events_shipment_time",
        "shipment_events",
        ["shipment_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shipment_events_shipment_time", table_name="shipment_events")
    op.drop_index("ix_shipment_events_shipment_id", table_name="shipment_events")
    op.drop_index(op.f("ix_shipment_events_id"), table_name="shipment_events")
    op.drop_table("shipment_events")

    op.drop_index("ix_shipments_status_eta", table_name="shipments")
    op.drop_index("ix_shipments_tracking", table_name="shipments")
    op.drop_index("ix_shipments_idempotency_key", table_name="shipments")
    op.drop_index("ix_shipments_reference", table_name="shipments")
    op.drop_index("ix_shipments_conversation_id", table_name="shipments")
    op.drop_index(op.f("ix_shipments_id"), table_name="shipments")
    op.drop_table("shipments")
