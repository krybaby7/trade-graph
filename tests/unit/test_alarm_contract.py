"""The price-alarm contract (T33): what an alarm may watch, who owns it, and what is refused. No library import."""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.contracts.alarms import (
    ALARM_DEPARTMENTS,
    AlarmDelivery,
    AlarmRequest,
    CandlePriceCondition,
    DeliveryBatch,
    IndicatorCondition,
    QuotePriceCondition,
    parse_condition,
)

_CONTRACT = Path(__file__).resolve().parents[2] / "src" / "trade_graph" / "contracts" / "alarms.py"


def _quote(**changes) -> QuotePriceCondition:
    values = dict(kind="quote_price", symbol="BTC/USD", relation="crosses_above", level="65000.5")
    values.update(changes)
    return QuotePriceCondition.model_validate(values)


def _indicator(**changes) -> IndicatorCondition:
    values = dict(kind="indicator", symbol="BTC/USD", interval_minutes=15, indicator="RSI",
                  parameters={"timeperiod": 14}, relation="crosses_below", level="30")
    values.update(changes)
    return IndicatorCondition.model_validate(values)


def test_three_kinds_of_condition_parse_from_plain_json() -> None:
    quote = parse_condition({"kind": "quote_price", "symbol": "ETH/USD", "price": "bid", "relation": "below",
                             "level": "2500"})
    candle = parse_condition({"kind": "candle_price", "symbol": "SOL/USD", "interval_minutes": 5,
                              "price": "close", "relation": "above", "level": "150.25"})
    indicator = parse_condition({"kind": "indicator", "symbol": "BTC/USD", "interval_minutes": 60,
                                 "indicator": "MACD", "output": "histogram", "relation": "crosses_above",
                                 "level": "0.5"})
    assert isinstance(quote, QuotePriceCondition) and quote.level == Decimal("2500") and quote.basis == "quote"
    assert isinstance(candle, CandlePriceCondition) and candle.source == "kraken_public_ohlc"
    assert candle.basis == "candle"
    assert isinstance(indicator, IndicatorCondition) and indicator.basis == "candle"
    # Defaults are filled in from the menu, so the stored condition says exactly what is computed.
    assert indicator.parameters == {"fastperiod": 12, "slowperiod": 26, "signalperiod": 9}


def test_levels_are_exact_decimals_and_floats_are_refused() -> None:
    assert _quote(level="65000.10").level == Decimal("65000.10")
    assert _quote(level=65000).level == Decimal("65000")
    for bad in (65000.5, "NaN", "Infinity", "abc", True, None, "1" * 65):
        with pytest.raises(ValidationError):
            _quote(level=bad)
    document = json.loads(_quote(level="65000.10").model_dump_json())
    assert document["level"] == "65000.1"  # canonical decimal text, never a JSON float


def test_price_levels_must_be_positive_but_indicator_levels_may_be_zero_or_negative() -> None:
    for bad in ("0", "-1"):
        with pytest.raises(ValidationError):
            _quote(level=bad)
        with pytest.raises(ValidationError):
            CandlePriceCondition.model_validate({"kind": "candle_price", "symbol": "BTC/USD",
                                                 "interval_minutes": 15, "relation": "above", "level": bad})
    assert _indicator(indicator="MACD", parameters={}, output="macd", level="-12.5").level == Decimal("-12.5")
    assert _indicator(indicator="MOM", parameters={}, level="0").level == Decimal("0")


def test_indicator_requests_are_checked_against_the_menu() -> None:
    with pytest.raises(ValidationError, match="not on the indicator menu"):
        _indicator(indicator="VWAP")
    with pytest.raises(ValidationError, match="no parameter"):
        _indicator(parameters={"period": 14})
    with pytest.raises(ValidationError, match="between"):
        _indicator(parameters={"timeperiod": 501})
    with pytest.raises(ValidationError, match="fastperiod must be below slowperiod"):
        _indicator(indicator="MACD", parameters={"fastperiod": 30, "slowperiod": 26}, output="macd")
    with pytest.raises(ValidationError, match="several outputs"):
        _indicator(indicator="BBANDS", parameters={})
    with pytest.raises(ValidationError, match="has no output"):
        _indicator(output="signal")
    assert _indicator().output == "value"  # the single output is named, so the record is explicit


def test_obv_levels_and_levels_outside_an_outputs_range_are_refused() -> None:
    with pytest.raises(ValidationError, match="OBV"):
        _indicator(indicator="OBV", parameters={}, level="1000")
    with pytest.raises(ValidationError, match="outside"):
        _indicator(level="101")  # RSI reads 0 to 100: an alarm above 101 could never fire
    with pytest.raises(ValidationError, match="outside"):
        _indicator(indicator="WILLR", parameters={}, level="5")  # Williams %R reads -100 to 0
    assert _indicator(level="100").level == Decimal("100")


def test_symbols_intervals_and_sources_follow_the_candle_store() -> None:
    for bad in ("btc/usd", "BTCUSD", "BTC/US", ""):
        with pytest.raises(ValidationError):
            _quote(symbol=bad)
    for bad in (2, 30, 240, 1440, "15", 15.0):
        with pytest.raises(ValidationError):
            _indicator(interval_minutes=bad)
    with pytest.raises(ValidationError):
        _indicator(source="somewhere")
    assert _indicator(source="synthetic").source == "synthetic"


def test_unknown_fields_relations_and_prices_are_refused() -> None:
    with pytest.raises(ValidationError):
        _quote(relation="touches")
    with pytest.raises(ValidationError):
        _quote(price="close")
    with pytest.raises(ValidationError):
        _quote(repeat=True)
    with pytest.raises(ValidationError):
        parse_condition({"kind": "news", "symbol": "BTC/USD"})


def test_a_request_names_one_owning_department_and_optional_references() -> None:
    request = AlarmRequest.model_validate({
        "department": "trader", "condition": _quote().model_dump(mode="json"), "portfolio_id": "paper-usd",
        "day_plan_ref": "plan:2026-10-16:BTC", "note": "Breakout above the range high; re-read the plan.",
        "request_id": "call-1"})
    assert request.department == "trader" and request.day_plan_ref == "plan:2026-10-16:BTC"
    assert AlarmRequest(department="leader", condition=_quote()).day_plan_ref is None
    assert set(ALARM_DEPARTMENTS) == {"trader", "research", "leader", "learning", "optimisation"}
    for bad in ({"department": "engineer"}, {"department": "owner"}, {"note": "x" * 281},
                {"note": "line\x00break"}, {"day_plan_ref": "plan with spaces"}, {"day_plan_ref": ""}):
        with pytest.raises(ValidationError):
            AlarmRequest.model_validate({"department": "trader", "condition": _quote(), **bad})


def test_there_is_no_count_or_frequency_setting_in_the_contract() -> None:
    """D116: the one-shot rule is the only limit. No field may cap how many alarms exist or how often they fire."""
    conditions = (QuotePriceCondition, CandlePriceCondition, IndicatorCondition)
    fields = set(AlarmRequest.model_fields) | {name for model in conditions for name in model.model_fields}
    for word in ("max", "limit", "cooldown", "throttle", "per_day", "quota", "budget", "repeat"):
        assert not [field for field in fields if word in field], word


def test_delivery_documents_are_plain_json() -> None:
    item = AlarmDelivery(delivery_id="d1", sequence=1, department="trader", kind="alarm_fired", alarm_id="a1",
                         generation=1, portfolio_id=None, created_at="2026-10-16T12:00:00.000000Z",
                         document={"value": "65001"})
    batch = DeliveryBatch(wake_id="w1", department="trader", status="claimed",
                          claimed_at="2026-10-16T12:05:00.000000Z", items=(item,))
    assert json.loads(batch.model_dump_json())["items"][0]["document"] == {"value": "65001"}
    with pytest.raises(ValidationError):
        AlarmDelivery(delivery_id="d2", sequence=2, department="trader", kind="alarm_fired", alarm_id=None,
                      generation=None, portfolio_id=None, created_at="2026-10-16T12:00:00.000000Z", document={})


def test_the_contract_imports_no_library_adapter_or_model_code() -> None:
    tree = ast.parse(_CONTRACT.read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    for name in imported:
        assert not name.startswith(("trade_graph.adapters", "trade_graph.application", "talib", "numpy")), name
