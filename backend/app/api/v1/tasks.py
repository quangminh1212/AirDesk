"""Task-tracking endpoints: create, list, update and queue statistics.

These endpoints back the operator work queue. Everything is audited: creating a
task and every transition on it writes an ``audit_logs`` row with the before /
after payload so a reviewer can replay how an item moved through the queue.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import ColumnElement, Select, and_, func, select

from app.api.deps import DbSession, OperatorUser, Pagination
from app.core.errors import NotFoundError, ValidationError
from app.models.task import CLOSED_TASK_STATUSES, Task, TaskStatus, TaskType
from app.schemas.common import Page
from app.schemas.task import (
    TaskAssigneeCount,
    TaskCreate,
    TaskRead,
    TaskStats,
    TaskStatusCount,
    TaskUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/tasks", tags=["tasks"])

#: Current ordering contract: most urgent first, then soonest due, then newest.
_DEFAULT_ORDER = (
    Task.priority.asc(),
    Task.due_at.asc(),
    Task.created_at.desc(),
)

#: Fields tracked in the audit ``before``/``after`` payloads.
_AUDITED_FIELDS = ("title", "description", "status", "priority", "assignee_id", "due_at")


def _snapshot(task: Task) -> dict[str, object | None]:
    """Serialise the audited subset of a task into JSON-safe primitives."""
    snapshot: dict[str, object | None] = {}
    for field in _AUDITED_FIELDS:
        value = getattr(task, field)
        if isinstance(value, datetime):
            snapshot[field] = value.isoformat()
        elif isinstance(value, TaskStatus):
            snapshot[field] = value.value
        else:
            snapshot[field] = value
    return snapshot


def _overdue_clauses() -> list[ColumnElement[bool]]:
    """Predicates selecting "past its due date and still open" tasks."""
    return [
        Task.due_at.is_not(None),
        Task.due_at < datetime.now(UTC),
        Task.status.not_in(CLOSED_TASK_STATUSES),
    ]


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work item (operator)",
)
async def create_task(db: DbSession, operator: OperatorUser, payload: TaskCreate) -> TaskRead:
    if payload.status is TaskStatus.DONE:
        raise ValidationError("A task cannot be created as 'done'; use PATCH to complete it.")

    task = Task(
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        task_type=payload.task_type,
        created_by_id=operator.id,
        assignee_id=payload.assignee_id,
        conversation_id=payload.conversation_id,
        booking_id=payload.booking_id,
        escalation_id=payload.escalation_id,
        document_id=payload.document_id,
        due_at=payload.due_at,
        meta=payload.meta,
    )
    db.add(task)
    await db.flush()
    await record_audit(
        db,
        action="task.created",
        entity_type="task",
        entity_id=task.id,
        actor_id=operator.id,
        after={
            "title": task.title,
            "status": task.status.value,
            "priority": task.priority,
            "task_type": task.task_type.value,
            "assignee_id": task.assignee_id,
        },
    )
    await db.commit()
    await db.refresh(task)
    return TaskRead.model_validate(task)


@router.get("", response_model=Page[TaskRead], summary="List work items")
async def list_tasks(
    db: DbSession,
    _user: OperatorUser,
    pagination: Pagination,
    status_filter: TaskStatus | None = Query(None, alias="status"),
    assignee_id: str | None = Query(None),
    task_type: TaskType | None = Query(None),
    overdue: bool | None = Query(
        None, description="true = only overdue open items, false = only non-overdue items."
    ),
) -> Page[TaskRead]:
    page, page_size, offset = pagination

    filters: list[ColumnElement[bool]] = []
    if status_filter is not None:
        filters.append(Task.status == status_filter)
    if assignee_id is not None:
        filters.append(Task.assignee_id == assignee_id)
    if task_type is not None:
        filters.append(Task.task_type == task_type)
    if overdue is not None:
        # ``overdue=false`` means "not overdue": either no deadline, or one in
        # the future, or already closed.
        clauses = _overdue_clauses()
        filters.extend(clauses if overdue else [~and_(*clauses)])

    stmt: Select[tuple[Task]] = select(Task)
    count_stmt = select(func.count()).select_from(Task)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = await db.scalar(count_stmt) or 0
    rows = (await db.scalars(stmt.order_by(*_DEFAULT_ORDER).limit(page_size).offset(offset))).all()
    return Page.create(
        [TaskRead.model_validate(t) for t in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{task_id}", response_model=TaskRead, summary="Update a work item")
async def update_task(
    db: DbSession, operator: OperatorUser, task_id: str, payload: TaskUpdate
) -> TaskRead:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found.")

    before = _snapshot(task)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(task, field, value)

    # Completing a task stamps the time; re-opening it clears the stamp again so
    # ``completed_at`` always describes the *current* completion.
    if "status" in changes:
        task.completed_at = datetime.now(UTC) if task.status is TaskStatus.DONE else None

    await record_audit(
        db,
        action="task.updated",
        entity_type="task",
        entity_id=task.id,
        actor_id=operator.id,
        before=before,
        after=_snapshot(task),
    )
    await db.commit()
    await db.refresh(task)
    return TaskRead.model_validate(task)


@router.get("/stats", response_model=TaskStats, summary="Work-queue statistics")
async def task_stats(db: DbSession, _user: OperatorUser) -> TaskStats:
    status_rows = (await db.execute(select(Task.status, func.count()).group_by(Task.status))).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    assignee_rows = (
        await db.execute(
            select(Task.assignee_id, func.count())
            .group_by(Task.assignee_id)
            .order_by(func.count().desc())
        )
    ).all()

    overdue = await db.scalar(select(func.count()).select_from(Task).where(*_overdue_clauses()))

    return TaskStats(
        total=sum(status_counts.values()),
        by_status=[TaskStatusCount(status=s, count=status_counts.get(s, 0)) for s in TaskStatus],
        by_assignee=[TaskAssigneeCount(assignee_id=row[0], count=row[1]) for row in assignee_rows],
        overdue=overdue or 0,
    )
