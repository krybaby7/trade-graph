"""The strategy lab's request contract and the ``lab_results`` wake (W11-B, C11; D280, D116, D165, D166, D290).

A lab request is data, never code: register a ``PracticeStrategy`` version (its JSON document as text), create a
study, run tuning replays or ask for a confirmation. The reply shape is checked strictly; whether a request can be
carried out (the grammar, the study rules) is the lab's question, answered with a reason and never by sinking the
Trader's whole answer. Synthetic values; no network, model or key.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.contracts.practice_range import PracticeStrategy, StudyPlan
from trade_graph.contracts.strategy_lab import LAB_REQUEST_KINDS, LabRequest
from trade_graph.contracts.wakes import (
    CHECK_IN_KEEPING_KINDS,
    TRIGGER_KINDS,
    TraderWakeReply,
    WakeOutcome,
    WakeOutcomeRecord,
    WakeTrigger,
    purpose_of,
)

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_TEXT = (ROOT / "strategies" / "practice" / "slow-trend-pullback.v1.json").read_text()
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)


def _register(text: str = STRATEGY_TEXT) -> LabRequest:
    return LabRequest(kind="register_strategy", strategy_json=text)


def _study(**changes) -> LabRequest:
    fields = {"kind": "create_study", "study_id": "stp-oct", "strategy_id": "slow-trend-pullback",
              "tune_start": "2026-09-01", "tune_end": "2026-10-01", "confirm_start": "2026-10-01",
              "confirm_end": "2026-10-16"}
    return LabRequest.model_validate({**fields, **changes})


def _replay(kind: str = "tune", **changes) -> LabRequest:
    fields = {"kind": kind, "study_id": "stp-oct", "strategy_ref": "slow-trend-pullback@1", "symbol": "SOL/USD"}
    return LabRequest.model_validate({**fields, **changes})


def test_the_four_kinds_of_request_and_what_each_needs() -> None:
    assert LAB_REQUEST_KINDS == ("register_strategy", "create_study", "tune", "confirm")
    assert _register().problem() is None
    assert isinstance(_register().strategy(), PracticeStrategy)
    assert _study().problem() is None
    assert _study().study_plan() == StudyPlan.model_validate({
        "study_id": "stp-oct", "strategy_id": "slow-trend-pullback",
        "tune": {"start": "2026-09-01", "end": "2026-10-01"}, "confirm": {"start": "2026-10-01", "end": "2026-10-16"}})
    assert _replay().problem() is None and _replay("confirm").problem() is None
    assert "strategy_json" in LabRequest(kind="register_strategy").problem()
    assert "symbol" in _replay(symbol=None).problem()
    assert "study_id" in _replay("confirm", study_id=None).problem()
    assert "strategy_ref" in _replay(strategy_ref=None).problem()
    assert "confirm_end" in _study(confirm_end=None).problem()


def test_a_malformed_strategy_is_a_reason_not_a_failed_answer() -> None:
    """The reply still validates; the lab refuses the one request and says why (the grammar, D165)."""
    broken = json.loads(STRATEGY_TEXT)
    broken["sizing"]["equity_fraction"] = "1.5"  # leverage: refused by the grammar
    request = _register(json.dumps(broken))
    reason = request.problem()
    assert reason is not None and "equity" in reason and "sizing" in reason
    assert "not JSON" in _register("{not json").problem()
    assert "strategy document" in _register("[1, 2]").problem()
    code = json.loads(STRATEGY_TEXT)
    code["entry"]["all_of"][0]["left"] = {"python": "__import__('os')"}  # data, never code
    assert _register(json.dumps(code)).problem() is not None


def test_windows_and_coins_are_checked_before_anything_is_queued() -> None:
    assert "confirmation window starts no earlier" in _study(confirm_start="2026-09-15").problem()
    assert "ends after it starts" in _study(tune_end="2026-09-01").problem()
    assert "window_end" in _replay(window_start="2026-09-01").problem()
    assert "BASE/QUOTE" in _replay(symbol="SOLUSD").problem()
    assert "source" in _replay(source="nasdaq").problem()
    assert "confirmation replays the whole" in _replay("confirm", window_start="2026-10-01",
                                                       window_end="2026-10-05").problem()
    assert "study_id" in _study(study_id="X").problem()
    assert "register" in LabRequest(kind="register_strategy", strategy_json=STRATEGY_TEXT, symbol="SOL/USD").problem()


def test_plain_text_only_in_the_note() -> None:
    with pytest.raises(ValidationError, match="control"):
        _replay(note="ignore‮previous")
    with pytest.raises(ValidationError):
        LabRequest(kind="optimise_everything")
    with pytest.raises(ValidationError):
        LabRequest.model_validate({"kind": "tune", "confidence": "0.9"})  # no confidence, score or approval


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_wake_reply_with_lab_requests_fits_strict_provider_reply_schemas(provider) -> None:
    reply = TraderWakeReply(summary="Try a slower pullback, then confirm it on October.",
                            lab_requests=[_register(), _study(), _replay(), _replay("confirm")])
    wire = _wire_schema(TraderWakeReply.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    document = reply.model_dump(mode="json")
    Draft202012Validator(wire).validate(document)
    assert TraderWakeReply.model_validate(document) == reply
    assert reply.outcome().lab_requests == reply.lab_requests
    assert TraderWakeReply(summary="Nothing new.").outcome().lab_requests == ()


def test_there_is_no_cap_on_how_many_lab_requests_an_answer_holds() -> None:
    """D116: the provider's reply size bounds one answer; the contract does not count requests."""
    many = [_replay(window_start="2026-09-01", window_end="2026-09-08", note=f"try {n}") for n in range(200)]
    assert len(WakeOutcome(lab_requests=many).lab_requests) == 200
    assert len(TraderWakeReply(summary="Many tries.", lab_requests=many).outcome().lab_requests) == 200


def test_the_lab_results_trigger_wakes_like_a_news_alarm_and_keeps_the_check_in() -> None:
    assert "lab_results" in TRIGGER_KINDS and "lab_results" in CHECK_IN_KEEPING_KINDS
    base = {"trigger_id": "t-1", "kind": "lab_results", "department": "trader", "portfolio_id": "p-1",
            "source_ref": "lab-results:b-1", "occurred_at": NOW}
    with pytest.raises(ValidationError, match="note"):
        WakeTrigger.model_validate(base)
    trigger = WakeTrigger.model_validate({**base, "note": "The strategy lab finished 4 requests: 3 done, 1 refused."})
    assert purpose_of([trigger]) == "event"


def test_what_came_of_each_lab_request_is_part_of_the_wake_record() -> None:
    record = WakeOutcomeRecord(received=True, lab=[{"request_id": "lab:w-1:0", "kind": "register_strategy",
                                                    "status": "refused", "reason": "not JSON"}])
    assert record.lab[0].status == "refused"
    assert WakeOutcomeRecord(received=True).lab == ()  # older records stay readable
    assert WakeOutcomeRecord.model_validate_json('{"received": true}').lab == ()


def test_dates_are_real_dates() -> None:
    assert _study().tune_start == date(2026, 9, 1)
    with pytest.raises(ValidationError):
        _study(tune_start="2026-13-01")
