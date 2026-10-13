"""Reporting / analytics response models.

The dashboard payloads are intentionally flat lists of small typed models rather
than giant dicts: the frontend charts bind to named fields, and Pydantic keeps
the ``null`` semantics explicit (a metric that has no data yet is ``None``, not
``0``, so a chart can render a gap instead of a fake zero).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ConversationMetrics(BaseModel):
    """Conversation funnel counters."""

    total: int = 0
    open: int = 0
    pending_human: int = 0
    resolved: int = 0


class MessageMetrics(BaseModel):
    """Message volume, including rolling windows."""

    total: int = 0
    last_24h: int = 0
    last_7d: int = 0


class BookingMetrics(BaseModel):
    """Booking volume and money, grouped by status and by currency."""

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    #: Gross booked value in minor units, keyed by ISO currency code.
    revenue_by_currency: dict[str, int] = Field(default_factory=dict)


class EscalationMetrics(BaseModel):
    """Human hand-off load and responsiveness."""

    total: int = 0
    open: int = 0
    #: Mean wall-clock time from creation to resolution, in minutes.
    avg_resolution_minutes: float | None = None


class AgentQualityMetrics(BaseModel):
    """How well the agent is doing, from the message/tool-call ledger."""

    #: Mean assistant-message confidence (``None`` when no scored turn exists).
    avg_confidence: float | None = None
    avg_latency_ms: float | None = None
    total_cost_usd: float = 0.0
    #: Tool calls that returned successfully divided by all tool calls.
    tool_success_rate: float | None = None
    tool_calls_total: int = 0
    tool_calls_failed: int = 0


class TaskMetrics(BaseModel):
    """Open work-queue pressure."""

    open: int = 0
    overdue: int = 0
    done_this_week: int = 0


class DocumentMetrics(BaseModel):
    """Document vault, grouped by document type."""

    total: int = 0
    by_doc_type: dict[str, int] = Field(default_factory=dict)


class OverviewReport(BaseModel):
    """Single payload powering the operator dashboard."""

    generated_at: str
    conversations: ConversationMetrics
    messages: MessageMetrics
    bookings: BookingMetrics
    escalations: EscalationMetrics
    agent_quality: AgentQualityMetrics
    tasks: TaskMetrics
    documents: DocumentMetrics


class VolumePoint(BaseModel):
    """One day of the volume time series."""

    date: date
    conversations_created: int = 0
    messages_created: int = 0
    bookings_created: int = 0
    escalations_created: int = 0


class VolumeReport(BaseModel):
    """Contiguous daily series (gaps are zero-filled so charts stay aligned)."""

    days: int
    start: date
    end: date
    points: list[VolumePoint] = Field(default_factory=list)


class AgentPerformancePoint(BaseModel):
    """Per-day agent quality metrics."""

    date: date
    turns: int = 0
    avg_confidence: float | None = None
    #: Escalations raised that day / turns that day.
    escalation_rate: float = 0.0
    avg_latency_ms: float | None = None
    total_cost_usd: float = 0.0
    #: Failed tool calls / all tool calls attributed to that day's turns.
    tool_failure_rate: float = 0.0
    tool_calls_total: int = 0
    tool_calls_failed: int = 0


class AgentPerformanceReport(BaseModel):
    """Contiguous daily series of agent quality metrics."""

    days: int
    start: date
    end: date
    points: list[AgentPerformancePoint] = Field(default_factory=list)
