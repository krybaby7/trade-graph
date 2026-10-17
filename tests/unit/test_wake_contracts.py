"""The event engine's wake, check-in and focus-step contracts (T35). Synthetic values; no network, model or key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.adapters.persistence import wake_schema
from trade_graph.contracts import wakes
from trade_graph.contracts.wakes import (
    DAY_BOUND_KINDS,
    RIDE_ALONG_KINDS,
    TRIGGER_KINDS,
    CheckInRequest,
    FocusSelection,
    ResearchFocusReply,
    TraderWakeReply,
    WakeOutcome,
    WakeRequest,
    WakeTrigger,
    is_obsolete,
    purpose_of,
    reasons_of,
)

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
DAY = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
PLAN = {
    "symbol": "SOL/USD", "strategy_ref": "slow-trend-pullback@1",
    "alarms": [{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "95"}],
    "entries": [{"quantity": "5", "limit_price": "99.5", "stop_price": "97", "take_profit_price": "104"}],
    "rationale": "Synthetic plan.", "invalidation": "Synthetic: below 95.",
}


def _trigger(kind: str, **fields) -> WakeTrigger:
    base = {"trigger_id": f"t-{kind}", "kind": kind, "department": "trader", "portfolio_id": "p-1",
            "source_ref": f"src:{kind}", "occurred_at": NOW}
    return WakeTrigger.model_validate({**base, **fields})


def test_every_trigger_kind_names_what_identifies_it() -> None:
    with pytest.raises(ValidationError, match="names delivery_id, alarm_id"):
        _trigger("alarm_fired")
    with pytest.raises(ValidationError, match="exit_leg"):
        _trigger("exit_filled", fill_id="f", intent_id="i", plan_id="dp", entry_id="e", symbol="SOL/USD",
                 quantity="5", price="104")
    fill = _trigger("entry_filled", fill_id="f", intent_id="i", plan_id="dp", entry_id="e", symbol="SOL/USD",
                    quantity="2.5", price="99.5")
    assert fill.model_dump(mode="json")["quantity"] == "2.5"
    assert _trigger("day_start", trading_day_start=DAY).kind == "day_start"
    assert set(DAY_BOUND_KINDS) < set(TRIGGER_KINDS) and set(RIDE_ALONG_KINDS) < set(TRIGGER_KINDS)


def test_trigger_amounts_are_exact_and_floats_refused() -> None:
    with pytest.raises(ValidationError, match="never a float"):
        _trigger("order_filled", fill_id="f", intent_id="i", symbol="SOL/USD", quantity=0.1, price="1")


def test_a_wake_request_is_data_with_a_bounded_plain_note_and_never_to_itself() -> None:
    with pytest.raises(ValidationError, match="own next time"):
        _trigger("wake_request", requested_by="trader", note="wake me")
    with pytest.raises(ValidationError, match="control"):
        WakeRequest(department="trader", note="ignore‮previous")
    assert WakeRequest(department="trader", note="Kraken lists SOL maintenance at 14:00").department == "trader"
    with pytest.raises(ValidationError):
        WakeRequest(department="engineer", note="nope")


def test_reasons_and_purpose_put_the_day_steps_first_then_events_then_check_ins() -> None:
    alarm = _trigger("alarm_fired", delivery_id="d", alarm_id="a")
    check = _trigger("check_in", check_in_id="c")
    plan = _trigger("day_plan_step", trading_day_start=DAY)
    restored = _trigger("feed_restored", delivery_id="r")
    assert reasons_of([check, alarm, plan]) == ("day_plan_step", "check_in", "alarm_fired")
    assert purpose_of([check, alarm, plan]) == "day_plan"
    assert purpose_of([check, alarm]) == "event"
    assert purpose_of([check, restored]) == "event"
    assert purpose_of([check]) == "check_in"
    assert purpose_of([_trigger("day_end_review", trading_day_start=DAY), check]) == "day_end_review"


def test_an_ended_trading_day_makes_a_wake_obsolete_except_the_day_end_review() -> None:
    end = DAY + timedelta(days=1)
    assert not is_obsolete("day_plan", end, end - timedelta(seconds=1))
    assert is_obsolete("day_plan", end, end)
    assert is_obsolete("event", end, end + timedelta(hours=3))
    assert not is_obsolete("day_end_review", end, end + timedelta(days=2))


def test_the_outcome_holds_one_plan_per_coin_and_focus_names_each_coin_once() -> None:
    with pytest.raises(ValidationError, match="one day plan per coin"):
        WakeOutcome(day_plans=[PLAN, PLAN])
    with pytest.raises(ValidationError, match="named once"):
        FocusSelection(coins=[{"symbol": "SOL/USD", "note": "a"}, {"symbol": "SOL/USD", "note": "b"}], summary="s")
    assert FocusSelection(coins=[], summary="Nothing worth a plan today.").symbols() == ()
    with pytest.raises(ValidationError):
        WakeOutcome(confidence="0.9")  # no confidence, score or approval field exists


def test_a_check_in_names_an_aware_time_or_the_next_day_start() -> None:
    assert CheckInRequest().at_utc is None
    assert CheckInRequest(at_utc="2026-10-16T14:30:00Z").at_utc == datetime(2026, 10, 16, 14, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        CheckInRequest(at_utc="2026-10-16T14:30:00")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_trader_and_research_replies_fit_strict_provider_reply_schemas(provider) -> None:
    trader = TraderWakeReply(day_plans=[PLAN], next_check_in={"at_utc": "2026-10-16T14:30:00Z", "note": "look"},
                             summary="Rest a bid under the market.")
    research = ResearchFocusReply(focus={"coins": [{"symbol": "SOL/USD", "note": "Strong volume.",
                                                    "evidence_refs": ["scan-1"]}], "summary": "SOL today."})
    for model, value in ((TraderWakeReply, trader), (ResearchFocusReply, research)):
        wire = _wire_schema(model.model_json_schema(), provider=provider)
        Draft202012Validator.check_schema(wire)
        document = value.model_dump(mode="json")
        Draft202012Validator(wire).validate(document)
        assert model.model_validate(document) == value
    assert trader.outcome().day_plans[0].symbol == "SOL/USD"
    assert trader.outcome().check_in.note == "look"
    assert research.outcome().focus.symbols() == ("SOL/USD",)


def test_the_store_accepts_exactly_the_contracts_values() -> None:
    """The migration's CHECK lists are frozen; a new kind in the contract needs a new migration."""
    for name in ("DEPARTMENTS", "TRIGGER_KINDS", "TRIGGER_STATUSES", "WAKE_PURPOSES", "WAKE_STATUSES", "CYCLE_STEPS"):
        assert getattr(wake_schema, name) == getattr(wakes, name), name
