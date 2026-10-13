"""Agent quality-assurance: automated checks, error capture and escalation.

This module implements the "verify the result, record the failure, hand the
work to a person" requirement. It is deliberately independent of the agent
loop so it can be reused to score archived transcripts offline.

Design
------
``QACheck`` produces a ``QAFinding`` when a rule is violated. ``QAEvaluator``
runs a set of checks over an agent turn and returns a ``QAReport`` with an
overall verdict. Findings at or above ``severity=error`` mark the turn as
failed, which the API layer turns into an escalation.

Checks are pure functions over ``QASubject``, which makes them trivially
unit-testable and safe to extend without touching the agent.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)

# Phrases that must never be emitted by a customer-facing agent without a
# verifiable tool result behind them.
_HALLUCINATION_MARKERS = (
    "i have booked",
    "i've booked",
    "đã đặt vé",
    "đã xuất vé",
    "your ticket is confirmed",
    "vé đã được xác nhận",
)

# A price-like token, used to detect invented fares.
_PRICE_RE = re.compile(r"\b\d{1,3}(?:[.,]\d{3}){2,}\b|\b\d{7,}\b")
_PNR_RE = re.compile(r"\b[A-Z0-9]{6}\b")


class Severity(str, enum.Enum):
    """How badly a finding hurts the turn."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.ERROR: 2,
    Severity.CRITICAL: 3,
}

# Score penalty applied per finding, by severity.
_SEVERITY_PENALTY = {
    Severity.INFO: 0.02,
    Severity.WARNING: 0.15,
    Severity.ERROR: 0.4,
    Severity.CRITICAL: 1.0,
}


class Verdict(str, enum.Enum):
    """Overall outcome of a QA evaluation."""

    PASS = "pass"
    REVIEW = "review"  # warn-level only: usable, but worth a human glance
    FAIL = "fail"  # error/critical: must not be sent unreviewed


@dataclass
class QASubject:
    """Everything a check may inspect about one completed agent turn."""

    reply: str
    user_message: str
    intent: str | None = None
    confidence: float | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    offers: list[dict[str, Any]] = field(default_factory=list)
    booking: dict[str, Any] | None = None
    escalated: bool = False
    conversation_id: str | None = None

    @property
    def tool_names(self) -> list[str]:
        return [t.get("tool", "") for t in self.tool_calls]

    @property
    def failed_tools(self) -> list[dict[str, Any]]:
        return [t for t in self.tool_calls if not t.get("success", True)]

    def used(self, tool: str) -> bool:
        return tool in self.tool_names


@dataclass
class QAFinding:
    """A single rule violation."""

    code: str
    message: str
    severity: Severity
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "details": self.details,
        }


@dataclass
class QAReport:
    """Result of evaluating one turn."""

    verdict: Verdict
    score: float
    findings: list[QAFinding] = field(default_factory=list)

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: _SEVERITY_ORDER[f.severity]).severity

    @property
    def failed(self) -> bool:
        return self.verdict is Verdict.FAIL

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "score": round(self.score, 3),
            "findings": [f.to_dict() for f in self.findings],
        }


class QACheck(Protocol):
    """A named rule that inspects a subject and may report a finding."""

    code: str
    severity: Severity

    def __call__(self, subject: QASubject) -> QAFinding | None: ...


# --- individual checks -----------------------------------------------------


def check_empty_reply(subject: QASubject) -> QAFinding | None:
    """An empty reply is always a defect."""
    if not subject.reply.strip():
        return QAFinding(
            code="empty_reply",
            message="Agent produced an empty reply.",
            severity=Severity.ERROR,
        )
    return None


def check_tool_failures(subject: QASubject) -> QAFinding | None:
    """Any failed tool call degrades the answer and needs review."""
    failed = subject.failed_tools
    if failed:
        return QAFinding(
            code="tool_failure",
            message=f"{len(failed)} tool call(s) failed during the turn.",
            severity=Severity.WARNING,
            details={"tools": [t.get("tool_name") for t in failed]},
        )
    return None


def check_unverified_booking_claim(subject: QASubject) -> QAFinding | None:
    """Claiming a booking was made without a successful ``create_booking``.

    This is the highest-risk failure mode for a ticketing agent: telling a
    customer they are ticketed when they are not.
    """
    text = subject.reply.lower()
    claimed = any(marker in text for marker in _HALLUCINATION_MARKERS)
    verified = subject.booking is not None or (
        subject.used("create_booking") and not subject.failed_tools
    )
    if claimed and not verified:
        return QAFinding(
            code="unverified_booking_claim",
            message="Reply claims a booking/ticket exists but no booking succeeded.",
            severity=Severity.CRITICAL,
        )
    return None


def check_unsourced_prices(subject: QASubject) -> QAFinding | None:
    """Prices quoted with no pricing tool behind them."""
    if not subject.offers and not subject.booking and _PRICE_RE.search(subject.reply):
        return QAFinding(
            code="unsourced_price",
            message="Reply quotes a money amount with no offer or booking to back it.",
            severity=Severity.ERROR,
        )
    return None


def check_unsourced_pnr(subject: QASubject) -> QAFinding | None:
    """A booking reference emitted without a booking record."""
    if subject.booking:
        return None
    if subject.used("create_booking"):
        return None
    if _PNR_RE.search(subject.reply) and "offer" not in subject.reply.lower():
        return QAFinding(
            code="unsourced_pnr",
            message="Reply contains a booking-reference-like token with no booking behind it.",
            severity=Severity.WARNING,
        )
    return None


def check_missing_escalation(subject: QASubject) -> QAFinding | None:
    """Low confidence that was never handed to a human."""
    if subject.confidence is not None and subject.confidence < 0.5 and not subject.escalated:
        return QAFinding(
            code="missing_escalation",
            message="Confidence was very low but the turn was not escalated.",
            severity=Severity.ERROR,
            details={"confidence": subject.confidence},
        )
    return None


def check_excessive_length(subject: QASubject) -> QAFinding | None:
    """Channel-unfriendly walls of text."""
    if len(subject.reply) > 4000:
        return QAFinding(
            code="reply_too_long",
            message="Reply exceeds 4000 characters.",
            severity=Severity.WARNING,
            details={"length": len(subject.reply)},
        )
    return None


DEFAULT_CHECKS: tuple[Callable[[QASubject], QAFinding | None], ...] = (
    check_empty_reply,
    check_unverified_booking_claim,
    check_unsourced_prices,
    check_tool_failures,
    check_missing_escalation,
    check_unsourced_pnr,
    check_excessive_length,
)


class QAEvaluator:
    """Runs a configurable set of checks and produces a verdict."""

    def __init__(self, checks: tuple[Callable[[QASubject], QAFinding | None], ...] | None = None):
        self._checks = checks if checks is not None else DEFAULT_CHECKS

    def evaluate(self, subject: QASubject) -> QAReport:
        findings: list[QAFinding] = []
        for check in self._checks:
            try:
                finding = check(subject)
            except Exception as exc:  # noqa: BLE001 - a broken check must not break the turn
                logger.warning(
                    "qa_check_error", check=getattr(check, "__name__", "?"), error=str(exc)
                )
                continue
            if finding is not None:
                findings.append(finding)

        worst = max((_SEVERITY_ORDER[f.severity] for f in findings), default=-1)
        if worst >= _SEVERITY_ORDER[Severity.ERROR]:
            verdict = Verdict.FAIL
        elif worst >= _SEVERITY_ORDER[Severity.WARNING]:
            verdict = Verdict.REVIEW
        else:
            verdict = Verdict.PASS

        # Score: start perfect, subtract by severity, floor at 0.
        penalty = sum(_SEVERITY_PENALTY[f.severity] for f in findings)
        report = QAReport(verdict=verdict, score=max(0.0, 1.0 - penalty), findings=findings)
        logger.info(
            "qa_evaluated",
            conversation_id=subject.conversation_id,
            verdict=verdict.value,
            codes=report.codes,
        )
        return report


_default_evaluator: QAEvaluator | None = None


def get_evaluator() -> QAEvaluator:
    """Return the process-wide default evaluator."""
    global _default_evaluator
    if _default_evaluator is None:
        _default_evaluator = QAEvaluator()
    return _default_evaluator
