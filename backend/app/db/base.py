"""SQLAlchemy declarative base and shared mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UUIDMixin:
    """Adds a string UUID primary key (portable across Postgres/SQLite)."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid, index=True)


class TimestampMixin:
    """Adds created_at / updated_at timestamps maintained by the DB."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
