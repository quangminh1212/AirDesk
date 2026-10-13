"""Schemas for the quality-assurance endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QAFindingRead(BaseModel):
    """One quality finding on a turn."""

    code: str
    message: str
    severity: str
    details: dict[str, Any] = Field(default_factory=dict)


class QATurnRead(BaseModel):
    """A reviewed agent turn."""

    model_config = ConfigDict(from_attributes=True)

    message_id: str
    conversation_id: str
    created_at: datetime
    verdict: str
    score: float
    confidence: float | None = None
    latency_ms: int = 0
    model: str | None = None
    cost_usd: float = 0.0
    content_preview: str = ""
    findings: list[QAFindingRead] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class QAOverview(BaseModel):
    """Aggregate quality health over a window."""

    window_days: int
    total_turns: int
    passed: int
    review: int
    failed: int
    pass_rate: float
    avg_score: float
    avg_confidence: float | None = None
    top_findings: dict[str, int] = Field(default_factory=dict)
