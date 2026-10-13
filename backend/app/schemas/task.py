"""Task-tracking schemas (create / update / read / stats)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import TaskStatus, TaskType


class TaskCreate(BaseModel):
    """Payload for ``POST /tasks``."""

    title: str = Field(..., min_length=2, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    status: TaskStatus = TaskStatus.TODO
    priority: int = Field(2, ge=1, le=3, description="1 = high, 2 = normal, 3 = low")
    task_type: TaskType = TaskType.OTHER
    assignee_id: str | None = Field(None, max_length=36)
    conversation_id: str | None = Field(None, max_length=36)
    booking_id: str | None = Field(None, max_length=36)
    escalation_id: str | None = Field(None, max_length=36)
    document_id: str | None = Field(None, max_length=120)
    due_at: datetime | None = None
    meta: dict[str, Any] | None = None


class TaskUpdate(BaseModel):
    """Partial update for ``PATCH /tasks/{task_id}``.

    Every field is optional: only the keys present in the request body are
    applied (``exclude_unset``), so a client can move a task to ``blocked``
    without resending the whole record.
    """

    title: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    status: TaskStatus | None = None
    priority: int | None = Field(None, ge=1, le=3)
    assignee_id: str | None = Field(None, max_length=36)
    due_at: datetime | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None = None
    status: TaskStatus
    priority: int
    task_type: TaskType
    created_by_id: str | None = None
    assignee_id: str | None = None
    conversation_id: str | None = None
    booking_id: str | None = None
    escalation_id: str | None = None
    document_id: str | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class TaskStatusCount(BaseModel):
    """One bucket of the by-status histogram (every status is always present)."""

    status: TaskStatus
    count: int = 0


class TaskAssigneeCount(BaseModel):
    """Workload per assignee (``assignee_id`` is null for the unassigned pile)."""

    assignee_id: str | None = None
    count: int = 0


class TaskStats(BaseModel):
    """Aggregated queue health for the operator dashboard."""

    total: int = 0
    by_status: list[TaskStatusCount] = Field(default_factory=list)
    by_assignee: list[TaskAssigneeCount] = Field(default_factory=list)
    overdue: int = 0


TaskRead.model_rebuild()
