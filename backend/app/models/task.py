"""Task-tracking model: work items for human operators.

The flight agent handles the happy path end to end, but some work can only be
finished by a person: a customer needs a callback, a refund has to be filed on
a carrier portal, a passport scan needs eyeballing. ``Task`` is the queue that
carries those items, optionally linked back to the conversation, booking,
escalation or document that produced them.

Priority follows the ``Escalation.priority`` convention (1 = high, 3 = low) so
the two queues can be merged and sorted with a single key.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class TaskStatus(str, enum.Enum):
    """Lifecycle of a work item."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"  # waiting on a third party (carrier, customer, ...)
    DONE = "done"
    CANCELLED = "cancelled"


class TaskType(str, enum.Enum):
    """What kind of work the item represents."""

    CUSTOMER_FOLLOWUP = "customer_followup"
    BOOKING_ISSUE = "booking_issue"
    REFUND = "refund"
    DOCUMENT_REVIEW = "document_review"
    LOGISTICS = "logistics"
    REPORT = "report"
    OTHER = "other"


#: Statuses that mean "no longer open" — used by the reporting aggregations.
CLOSED_TASK_STATUSES: tuple[TaskStatus, ...] = (TaskStatus.DONE, TaskStatus.CANCELLED)


class Task(UUIDMixin, TimestampMixin, Base):
    """A unit of work assigned to a human operator."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_status_due", "status", "due_at"),)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=16),
        default=TaskStatus.TODO,
        nullable=False,
    )
    #: 1 = high .. 3 = low, matching ``Escalation.priority``.
    priority: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, native_enum=False, length=24),
        default=TaskType.OTHER,
        nullable=False,
    )

    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assignee_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    escalation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("escalations.id", ondelete="SET NULL"), nullable=True
    )
    #: Free-form identifier of an uploaded document (vault lives outside the DB).
    document_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task {self.title!r} status={self.status.value} priority={self.priority}>"
