"""ticket status enum and per-document lifecycle

Turns ``tickets`` from a stub into a real back-office ticket record for the
airline agency desk: each row is one issued document that can later be voided,
refunded, or exchanged.

What changes:

* ``tickets.status`` becomes a ``VARCHAR(20)`` with a *checked* value domain
  (``issued`` / ``refunded`` / ``void`` / ``exchanged`` / ``used``) instead of
  free text. Free text is how statuses silently rot: ``"Refunded"`` and
  ``"refunded"`` are two different strings to a database and neither raises.
* Per-document money (``fare_amount`` / ``tax_amount`` / ``refund_amount``) so a
  refund is attributable to the exact coupon it came from, not only to the
  booking total.
* Lifecycle timestamps (``issued_at`` / ``voided_at`` / ``refunded_at`` /
  ``exchanged_at``) plus ``exchanged_from`` to keep the reissue chain.
* ``pnr`` denormalised onto the ticket so a carrier record locator can be looked
  up without joining back to the booking.

Backfill: every existing row was written by the old code with the literal
default ``"issued"``, so the value domain already holds. Rows with anything
else (only possible via manual SQL) are normalised to the same value rather
than left to violate the check constraint.

Design notes:

* ``native_enum=False`` with the model's exact ``length`` so PostgreSQL and
  SQLite emit identical DDL and the same suite passes on both.
* Money columns are ``BIGINT`` minor units — VND has no decimals, so 1 VND is
  1 minor unit and floats never touch money.
* The check constraint is named explicitly; PostgreSQL and SQLite both accept
  the same ``CHECK`` syntax, and Alembic can drop it by name on downgrade.

Revision ID: e5c9a3f17d84
Revises: d8b4f2a61c05
Create Date: 2026-10-15 09:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5c9a3f17d84"
down_revision: str | None = "d8b4f2a61c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TICKET_STATUSES = ("issued", "refunded", "void", "exchanged", "used")

_TICKET_STATUS_CHECK = "ck_tickets_status_valid"

#: Value the domain is backfilled and constrained to.
_DEFAULT_TICKET_STATUS = "issued"

#: Quoted SQL list, built from the tuple above so the two can never drift.
#: Bound parameters cannot be used for ``IN`` — SQLAlchemy renders ``IN ?`` and
#: SQLite rejects it, so the list is inlined from a source we control.
_STATUS_LIST_SQL = ", ".join(f"'{value}'" for value in _TICKET_STATUSES)


def upgrade() -> None:
    # --- status: free text -> checked value domain --------------------------
    # Added as nullable first, backfilled, then made NOT NULL: on a non-empty
    # table a direct NOT NULL ADD COLUMN relies on the server default being
    # applied to existing rows, which is not guaranteed across backends.
    op.add_column("tickets", sa.Column("pnr", sa.String(length=20), nullable=True))
    op.add_column(
        "tickets",
        sa.Column("fare_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tickets",
        sa.Column("tax_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tickets",
        sa.Column("refund_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("tickets", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tickets", sa.Column("exchanged_from", sa.String(length=32), nullable=True)
    )
    op.add_column("tickets", sa.Column("notes", sa.Text(), nullable=True))

    # Normalise anything outside the new domain, then pin the domain down.
    op.execute(
        f"UPDATE tickets SET status = '{_DEFAULT_TICKET_STATUS}' "
        f"WHERE status IS NULL OR status NOT IN ({_STATUS_LIST_SQL})"
    )

    # Existing rows predate explicit issuance tracking; the row's creation time
    # is the best available estimate and keeps ``issued_at`` monotonic.
    op.execute(
        "UPDATE tickets SET issued_at = created_at "
        f"WHERE issued_at IS NULL AND status = '{_DEFAULT_TICKET_STATUS}'"
    )

    # SQLite cannot ``ALTER ... ADD CONSTRAINT``; batch mode rebuilds the table
    # with the constraint inlined. This is the documented Alembic path and it
    # also produces a plain ``ALTER TABLE`` on PostgreSQL.
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=_DEFAULT_TICKET_STATUS,
        )
        batch_op.create_check_constraint(
            _TICKET_STATUS_CHECK,
            sa.column("status").in_(_TICKET_STATUSES),
        )


def downgrade() -> None:
    # SQLite cannot ``ALTER ... DROP CONSTRAINT``; batch mode rebuilds the table
    # instead, which is the documented Alembic path and also works on Postgres.
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_constraint(_TICKET_STATUS_CHECK, type_="check")
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=_DEFAULT_TICKET_STATUS,
        )
        batch_op.drop_column("notes")
        batch_op.drop_column("exchanged_from")
        batch_op.drop_column("exchanged_at")
        batch_op.drop_column("refunded_at")
        batch_op.drop_column("voided_at")
        batch_op.drop_column("issued_at")
        batch_op.drop_column("refund_amount")
        batch_op.drop_column("tax_amount")
        batch_op.drop_column("fare_amount")
        batch_op.drop_column("pnr")
