"""Tests for the agent QA/evaluation harness."""

from __future__ import annotations

from app.agents.qa import (
    QAEvaluator,
    QASubject,
    Severity,
    Verdict,
    check_empty_reply,
    check_excessive_length,
    check_missing_escalation,
    check_tool_failures,
    check_unsourced_pnr,
    check_unsourced_prices,
    check_unverified_booking_claim,
)

EVAL = QAEvaluator()


def _subject(**kw) -> QASubject:
    base = {"reply": "Chuyến bay của bạn đã sẵn sàng.", "user_message": "tìm vé"}
    base.update(kw)
    return QASubject(**base)


# --- individual checks -----------------------------------------------------


def test_empty_reply_is_error():
    f = check_empty_reply(_subject(reply="   "))
    assert f is not None and f.severity is Severity.ERROR


def test_normal_reply_has_no_finding():
    assert check_empty_reply(_subject()) is None


def test_tool_failure_is_warning():
    calls = [{"tool_name": "search_flights", "success": False}]
    f = check_tool_failures(_subject(tool_calls=calls))
    assert f is not None and f.severity is Severity.WARNING
    assert f.details["tools"] == ["search_flights"]


def test_no_tool_failure_when_all_succeed():
    calls = [{"tool_name": "search_flights", "success": True}]
    assert check_tool_failures(_subject(tool_calls=calls)) is None


def test_booking_claim_without_booking_is_critical():
    f = check_unverified_booking_claim(_subject(reply="I have booked your flight to Hanoi."))
    assert f is not None and f.severity is Severity.CRITICAL


def test_booking_claim_with_booking_is_fine():
    f = check_unverified_booking_claim(
        _subject(
            reply="I have booked your flight.",
            booking={"reference": "ABC123"},
        )
    )
    assert f is None


def test_vietnamese_booking_claim_detected():
    f = check_unverified_booking_claim(_subject(reply="Tôi đã đặt vé cho bạn rồi nhé."))
    assert f is not None and f.severity is Severity.CRITICAL


def test_unsourced_price_is_error():
    f = check_unsourced_prices(_subject(reply="Giá vé là 1.450.000 VND cho chuyến này."))
    assert f is not None and f.severity is Severity.ERROR


def test_price_with_offer_is_fine():
    f = check_unsourced_prices(
        _subject(
            reply="Giá vé là 1.450.000 VND.",
            offers=[{"offer_id": "x", "total_amount": 1450000}],
        )
    )
    assert f is None


def test_unsourced_pnr_warns():
    f = check_unsourced_pnr(_subject(reply="Mã đặt chỗ của bạn là AB12CD."))
    assert f is not None and f.severity is Severity.WARNING


def test_pnr_with_booking_is_fine():
    f = check_unsourced_pnr(_subject(reply="Mã đặt chỗ AB12CD.", booking={"reference": "AB12CD"}))
    assert f is None


def test_missing_escalation_on_low_confidence():
    f = check_missing_escalation(_subject(confidence=0.3, escalated=False))
    assert f is not None and f.severity is Severity.ERROR


def test_low_confidence_escalated_is_fine():
    assert check_missing_escalation(_subject(confidence=0.3, escalated=True)) is None


def test_excessive_length_warns():
    f = check_excessive_length(_subject(reply="x" * 4001))
    assert f is not None and f.severity is Severity.WARNING


# --- evaluator verdicts ----------------------------------------------------


def test_clean_turn_passes():
    report = EVAL.evaluate(
        _subject(
            reply="Đây là các chuyến bay phù hợp.",
            confidence=0.9,
            tool_calls=[{"tool": "search_flights", "success": True}],
            offers=[{"offer_id": "a"}],
        )
    )
    assert report.verdict is Verdict.PASS
    assert report.score == 1.0
    assert not report.failed


def test_warning_only_turn_is_review():
    report = EVAL.evaluate(
        _subject(
            reply="Hệ thống tạm thời gián đoạn.",
            confidence=0.9,
            tool_calls=[{"tool": "search_flights", "success": False}],
        )
    )
    assert report.verdict is Verdict.REVIEW
    assert not report.failed
    assert "tool_failure" in report.codes


def test_critical_turn_fails():
    report = EVAL.evaluate(_subject(reply="I have booked your ticket.", confidence=0.9))
    assert report.verdict is Verdict.FAIL
    assert report.failed
    assert "unverified_booking_claim" in report.codes


def test_empty_reply_fails():
    report = EVAL.evaluate(_subject(reply=""))
    assert report.verdict is Verdict.FAIL


def test_score_decreases_with_severity():
    clean = EVAL.evaluate(_subject(reply="ok", confidence=0.9))
    warned = EVAL.evaluate(
        _subject(reply="ok", confidence=0.9, tool_calls=[{"tool": "t", "success": False}])
    )
    failed = EVAL.evaluate(_subject(reply="I have booked it.", confidence=0.9))
    assert clean.score > warned.score > failed.score


def test_report_serialises():
    report = EVAL.evaluate(_subject(reply=""))
    data = report.to_dict()
    assert data["verdict"] == "fail"
    assert isinstance(data["findings"], list)
    assert data["findings"][0]["code"] == "empty_reply"


def test_broken_check_does_not_break_evaluation():
    def exploding(_subject):  # noqa: ANN001
        raise RuntimeError("boom")

    evaluator = QAEvaluator(checks=(exploding, check_empty_reply))
    report = evaluator.evaluate(_subject(reply=""))
    # The broken check is skipped; the working one still reports.
    assert report.codes == ["empty_reply"]
