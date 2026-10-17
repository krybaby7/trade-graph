"""The day-plan contract (T34): what the Trader writes for one focus coin. Synthetic plans only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.contracts.alarms import IndicatorCondition, QuotePriceCondition
from trade_graph.contracts.day_plans import DayPlan, PlanAlarm, PlanEntry, expiry_of

D = Decimal


def _plan(**changes) -> dict:
    document = {
        "symbol": "SOL/USD",
        "strategy_ref": "slow-trend-pullback@1",
        "indicators": [{"indicator": "RSI", "interval_minutes": 15, "parameters": [{"name": "timeperiod",
                                                                                    "value": 14}]}],
        "alarms": [{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "95",
                    "note": "wake me if the pullback deepens"}],
        "entries": [{"quantity": "2", "limit_price": "98.5", "stop_price": "96", "take_profit_price": "104"}],
        "rationale": "SOL pulled back to its slower average inside a rising trend; rest two small bids.",
        "invalidation": "A close below 95 on the 15-minute candles.",
    }
    document.update(changes)
    return document


def test_a_plan_names_its_coin_strategy_indicators_alarms_entries_and_rationale() -> None:
    plan = DayPlan.model_validate(_plan())
    assert (plan.symbol, plan.strategy_ref, plan.strategy_id) == ("SOL/USD", "slow-trend-pullback@1",
                                                                  "slow-trend-pullback")
    entry = plan.entries[0]
    assert (entry.quantity, entry.limit_price, entry.stop_price, entry.take_profit_price) == (
        D("2"), D("98.5"), D("96"), D("104"))
    assert plan.indicators[0].resolved_parameters() == {"timeperiod": 14}
    assert isinstance(plan.alarms[0].condition(), QuotePriceCondition)
    document = plan.canonical_document()
    assert document["entries"][0]["limit_price"] == "98.5"  # decimals are text, never floats
    assert DayPlan.model_validate(document) == plan
    assert len(plan.content_sha256) == 64 and DayPlan.model_validate(document).content_sha256 == plan.content_sha256


def test_the_contract_carries_no_confidence_approval_or_committee() -> None:
    names = set(DayPlan.model_fields) | set(PlanEntry.model_fields)
    for forbidden in ("confidence", "probability", "score", "approval", "approved_by", "committee", "vote",
                      "leader_approval", "threshold"):
        assert not any(forbidden in name for name in names)
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(confidence="0.9"))  # an unknown field is refused, never read


@pytest.mark.parametrize("entry, message", [
    ({"quantity": 2.0, "limit_price": "98.5", "stop_price": "96"}, "float"),
    ({"quantity": "2", "limit_price": 98.5, "stop_price": "96"}, "float"),
    ({"quantity": "0", "limit_price": "98.5", "stop_price": "96"}, "positive"),
    ({"quantity": "-1", "limit_price": "98.5", "stop_price": "96"}, "positive"),
    ({"quantity": "2", "limit_price": "98.5", "stop_price": "98.5"}, "below"),
    ({"quantity": "2", "limit_price": "98.5", "stop_price": "99"}, "below"),
    ({"quantity": "2", "limit_price": "98.5", "stop_price": "96", "take_profit_price": "98.5"}, "above"),
    ({"quantity": "2", "limit_price": "98.5", "stop_price": "96", "side": "sell"}, "Extra"),
    ({"quantity": "2", "limit_price": "98.5"}, "stop_price"),
    ({"quantity": "2", "limit_price": "1" * 41, "stop_price": "96"}, "too long"),
])
def test_entries_are_exact_positive_long_only_and_always_protected(entry, message) -> None:
    with pytest.raises(ValidationError, match=message):
        DayPlan.model_validate(_plan(entries=[entry]))


def test_a_plan_does_something_but_need_not_trade() -> None:
    alarms_only = DayPlan.model_validate(_plan(entries=[]))
    assert alarms_only.entries == () and len(alarms_only.alarms) == 1
    with pytest.raises(ValidationError, match="no_action"):
        DayPlan.model_validate(_plan(entries=[], alarms=[]))


@pytest.mark.parametrize("indicator", [
    {"indicator": "MAGIC", "interval_minutes": 15},
    {"indicator": "RSI", "interval_minutes": 30},
    {"indicator": "RSI", "interval_minutes": 15, "parameters": [{"name": "timeperiod", "value": 1}]},
    {"indicator": "RSI", "interval_minutes": 15, "parameters": [{"name": "nonsense", "value": 3}]},
    {"indicator": "RSI", "interval_minutes": 15, "parameters": [{"name": "timeperiod", "value": 14},
                                                                {"name": "timeperiod", "value": 15}]},
])
def test_indicators_come_from_the_menu_with_bounded_settings(indicator) -> None:
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(indicators=[indicator]))


def test_alarms_become_the_alarm_contract_conditions() -> None:
    alarm = PlanAlarm.model_validate({"kind": "indicator", "symbol": "SOL/USD", "relation": "crosses_below",
                                      "level": "30", "interval_minutes": 15, "indicator": "RSI",
                                      "parameters": [{"name": "timeperiod", "value": 14}]})
    condition = alarm.condition()
    assert isinstance(condition, IndicatorCondition)
    assert (condition.indicator, condition.output, condition.parameters) == ("RSI", "value", {"timeperiod": 14})
    candle = PlanAlarm.model_validate({"kind": "candle_price", "symbol": "SOL/USD", "relation": "below",
                                       "level": "95", "interval_minutes": 5, "price": "low"})
    assert candle.condition().kind == "candle_price"


@pytest.mark.parametrize("alarm", [
    {"kind": "quote_price", "symbol": "SOL/USD", "relation": "above", "level": 101.5},
    {"kind": "quote_price", "symbol": "SOL/USD", "relation": "above", "level": "101", "interval_minutes": 5},
    {"kind": "quote_price", "symbol": "SOL/USD", "relation": "above", "level": "-1"},
    {"kind": "candle_price", "symbol": "SOL/USD", "relation": "above", "level": "101"},
    {"kind": "indicator", "symbol": "SOL/USD", "relation": "above", "level": "101", "interval_minutes": 15,
     "indicator": "OBV"},
    {"kind": "indicator", "symbol": "SOL/USD", "relation": "above", "level": "101", "interval_minutes": 15,
     "indicator": "RSI"},
    {"kind": "indicator", "symbol": "SOL/USD", "relation": "above", "level": "50", "interval_minutes": 15,
     "indicator": "RSI", "price": "close"},
    {"kind": "quote_price", "symbol": "sol-usd", "relation": "above", "level": "101"},
])
def test_invalid_alarms_are_refused_with_the_plan(alarm) -> None:
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(alarms=[alarm]))


def test_text_is_concise_and_plain() -> None:
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(rationale="x" * 1001))
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(rationale="buy‮yes"))
    with pytest.raises(ValidationError):
        DayPlan.model_validate(_plan(strategy_ref="slow-trend-pullback"))


def test_an_entry_rests_until_its_own_expiry_or_the_end_of_the_trading_day() -> None:
    day_end = datetime(2026, 10, 17, tzinfo=UTC)
    plan = DayPlan.model_validate(_plan(entries=[
        {"quantity": "1", "limit_price": "98", "stop_price": "96"},
        {"quantity": "1", "limit_price": "97", "stop_price": "95", "expires_at_utc": "2026-10-16T18:00:00Z"},
    ]))
    assert expiry_of(plan.entries[0], day_end) == day_end
    assert expiry_of(plan.entries[1], day_end) == datetime(2026, 10, 16, 18, tzinfo=UTC)
    with pytest.raises(ValidationError):
        PlanEntry.model_validate({"quantity": "1", "limit_price": "98", "stop_price": "96",
                                  "expires_at_utc": "2026-10-16T18:00:00"})  # an aware time is required


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_plan_fits_a_strict_provider_reply_schema_for_the_event_engine(provider) -> None:
    wire = _wire_schema(DayPlan.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    full = DayPlan.model_validate(_plan(alarms=[
        {"kind": "indicator", "symbol": "SOL/USD", "relation": "crosses_below", "level": "30",
         "interval_minutes": 15, "indicator": "RSI", "parameters": [{"name": "timeperiod", "value": 14}]},
    ], entries=[{"quantity": "2", "limit_price": "98.5", "stop_price": "96",
                 "expires_at_utc": "2026-10-16T18:00:00Z"}]))
    value = full.model_dump(mode="json")
    Draft202012Validator(wire).validate(value)
    assert DayPlan.model_validate(value) == full
