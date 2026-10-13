"""booking contact fields

The agency must be able to reach the customer about an order. ``contact_email``
and ``contact_phone`` were accepted by ``POST /bookings`` and forwarded to the
provider, but never persisted, so the bookings list/detail always rendered an
empty contact column.

Revision ID: f7d1b6c28e30
Revises: e5c9a3f17d84
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7d1b6c28e30"
down_revision: str | None = "e5c9a3f17d84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("contact_email", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("contact_phone", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bookings") as batch:
        batch.drop_column("contact_phone")
        batch.drop_column("contact_email")
