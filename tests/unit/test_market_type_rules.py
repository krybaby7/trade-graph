"""The market-type rules and the labelling function in the protected kernel (W11-A, C10; D285, D286).

Plain values in, labels out: the rules document ``market-type-rules@1`` with its bounds and hash, the four-way
classification on 12-significant-digit comparisons (D136), the history and gap rules, the lag rule and the point-in-time
rule. The indicator numbers come from the application (TA-Lib); here they are written by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.kernel.authority import path_is_protected
from trade_graph.kernel.market_type import (
    CURRENT_MARKET_TYPE_RULES,
    MARKET_TYPE_RULES_V1,
    UNCLEAR_REASONS,
    MarketTypeRules,
    MarketTypeTimeline,
    SlotFacts,
    classify,
    label_at,
    resolve_market_type_rules,
    slot_verdicts,
)

D = Decimal
HOUR = timedelta(hours=1)
T0 = datetime(2026, 9, 1, tzinfo=UTC)  # the close of slot 0
RULES = MARKET_TYPE_RULES_V1


def test_version_one_stays_as_documented() -> None:
    # Version 2 (D230) is the default now; version 1 keeps every value and its pinned hash for its stored labels.
    assert CURRENT_MARKET_TYPE_RULES.ref == "market-type-rules@2"
    rules = MARKET_TYPE_RULES_V1
    assert rules.ref == "market-type-rules@1"
    assert (rules.interval_minutes, rules.average_period, rules.slope_bars, rules.strength_period,
            rules.trending_strength, rules.max_lag_bars) == (60, 168, 24, 14, D("20"), 2)
    assert rules.indicator_settings() == {
        "average": ("SMA", {"timeperiod": 168}), "strength": ("ADX", {"timeperiod": 14}),
        "plus": ("PLUS_DI", {"timeperiod": 14}), "minus": ("MINUS_DI", {"timeperiod": 14})}
    document = rules.canonical_document()
    assert document["indicators"]["strength"] == {"indicator": "ADX", "parameters": {"timeperiod": 14}}
    assert document["trending_strength"] == "20"
    # Pinned: stored labels and reports name this hash; any change to the rules is a new version (D285).
    assert rules.content_sha256 == "d017d69a24138f9c23e48f711a235b2fc6b2e8fbd930b53f2a676eff5718bb01"
    assert rules.content_sha256 == MarketTypeRules.model_validate(rules.model_dump(mode="json")).content_sha256
    assert resolve_market_type_rules("market-type-rules@1") is rules
    with pytest.raises(ValidationFailure, match="no market-type rules"):
        resolve_market_type_rules("market-type-rules@9")


def _rules(**changes) -> MarketTypeRules:
    return MarketTypeRules.model_validate({**MARKET_TYPE_RULES_V1.model_dump(mode="json"), **changes})


@pytest.mark.parametrize("changes", [
    {"interval_minutes": 5}, {"interval_minutes": 1440}, {"average_period": 10}, {"average_period": 501},
    {"slope_bars": 0}, {"slope_bars": 169}, {"strength_period": 3}, {"strength_period": 51},
    {"trending_strength": "9"}, {"trending_strength": "41"}, {"trending_strength": 20.0}, {"max_lag_bars": 7},
    {"max_lag_bars": -1}, {"version": 0}, {"rules_id": "other"}, {"unknown": 1}, {"slope_bars": True},
])
def test_every_parameter_is_bounded(changes) -> None:
    with pytest.raises((ValidationError, ValidationFailure)):
        _rules(**changes)


def test_any_change_is_a_new_hash() -> None:
    assert _rules(version=2).content_sha256 != RULES.content_sha256
    assert _rules(trending_strength="25").content_sha256 != RULES.content_sha256
    assert _rules(trending_strength="20.0").content_sha256 == RULES.content_sha256  # the same number


@pytest.mark.parametrize(("values", "expected", "checks"), [
    # close, average, average a day earlier, ADX, +DI, -DI
    (("110", "105", "104", "30", "28", "12"), "rising",
     {"strength": "trending", "pressure": "buyers", "price": "above_average", "average": "rising"}),
    (("90", "95", "96", "30", "10", "26"), "falling",
     {"strength": "trending", "pressure": "sellers", "price": "below_average", "average": "falling"}),
    (("110", "105", "104", "19.99", "28", "12"), "sideways",
     {"strength": "weak", "pressure": "buyers", "price": "above_average", "average": "rising"}),
    (("110", "105", "104", "20", "28", "12"), "rising",  # at the threshold counts as trending
     {"strength": "trending", "pressure": "buyers", "price": "above_average", "average": "rising"}),
    (("110", "105", "106", "30", "28", "12"), "sideways",  # the average turned down: the signs disagree
     {"strength": "trending", "pressure": "buyers", "price": "above_average", "average": "falling"}),
    (("100", "100", "100", "0", "0", "0"), "sideways",  # no movement at all
     {"strength": "weak", "pressure": "even", "price": "at_average", "average": "flat"}),
    (("90", "95", "96", "30", "26", "26"), "sideways",  # neither side ahead
     {"strength": "trending", "pressure": "even", "price": "below_average", "average": "falling"}),
])
def test_classification_needs_strength_and_four_agreeing_signs(values, expected, checks) -> None:
    close, average, before, strength, plus, minus = (D(item) for item in values)
    assert classify(close=close, average=average, average_before=before, strength=strength, plus=plus,
                    minus=minus, rules=RULES) == (expected, checks)


def test_comparisons_use_twelve_significant_digits() -> None:
    """Library noise in the last digits of a running sum neither makes nor breaks a trend (D136)."""
    noisy = dict(close=D("63000.10000000006"), average=D("63000.09999999999"), average_before=D("63000.1"),
                 strength=D("19.99999999999999"), plus=D("28"), minus=D("12"), rules=RULES)
    market_type, checks = classify(**noisy)
    assert market_type == "sideways"
    assert checks == {"strength": "trending", "pressure": "buyers", "price": "at_average", "average": "flat"}


def _facts(count: int, *, missing=(), close="110", average="105", strength="30", plus="28", minus="12",
           rising_average=True) -> list[SlotFacts]:
    facts = []
    for index in range(count):
        at = T0 + HOUR * index
        if index in missing:
            facts.append(SlotFacts(close_time=at, present=False))
            continue
        base = D(average) + (D("0.01") * index if rising_average else D("-0.01") * index)
        facts.append(SlotFacts(close_time=at, present=True, close=D(close) + D("0.01") * index, average=base,
                               strength=D(strength), plus=D(plus), minus=D(minus)))
    return facts


def _labels(facts, history: int, rules: MarketTypeRules = RULES) -> list[str | None]:
    return [verdict.market_type for verdict in slot_verdicts(facts, rules, history=history)]


def test_a_slot_is_labelled_only_with_its_whole_history_present() -> None:
    verdicts = slot_verdicts(_facts(40), RULES, history=30)
    assert [item.market_type for item in verdicts[:29]] == ["unclear"] * 29
    assert {item.reason for item in verdicts[:29]} == {"not_enough_candles"}
    assert [item.market_type for item in verdicts[29:]] == ["rising"] * 11
    assert verdicts[29].values == {"close": "110.29", "average": "105.29", "average_before": "105.05",
                                   "strength": "30", "plus": "28", "minus": "12"}


def test_a_gap_withholds_the_label_for_one_history_and_missing_oldest_slots_are_too_few_candles() -> None:
    """The reason reads only the slots the label needs, so it is the same in a short live read and a long replay."""
    labels = slot_verdicts(_facts(80, missing={0, 1, 2, 45}), RULES, history=30)
    assert labels[31].reason == "not_enough_candles" and labels[31].market_type == "unclear"
    assert labels[32].market_type == "rising"  # slots 3..32: complete
    assert labels[44].market_type == "rising"
    assert labels[45].present is False and labels[45].market_type is None
    assert {labels[index].reason for index in range(46, 74)} == {"gap"}
    # Slots 45..74: the missing slot is the oldest one needed, so fewer than 30 complete candles end here.
    assert labels[74].reason == "not_enough_candles"
    assert labels[75].market_type == "rising"  # slots 46..75 hold no missing slot again


def test_a_missing_indicator_value_is_unclear_never_guessed() -> None:
    facts = _facts(40)
    facts[35] = SlotFacts(close_time=facts[35].close_time, present=True, close=D("1"), average=D("1"),
                          strength=None, plus=D("1"), minus=D("1"))
    verdicts = slot_verdicts(facts, RULES, history=30)
    assert (verdicts[35].market_type, verdicts[35].reason) == ("unclear", "no_value")
    facts[36 - RULES.slope_bars] = SlotFacts(close_time=facts[36 - RULES.slope_bars].close_time, present=True,
                                             close=D("1"), average=None, strength=D("1"), plus=D("1"), minus=D("1"))
    assert slot_verdicts(facts, RULES, history=30)[36].reason == "no_value"


def test_the_history_covers_at_least_the_slope() -> None:
    with pytest.raises(ValidationFailure, match="history"):
        slot_verdicts(_facts(40), RULES, history=RULES.slope_bars)


def test_slots_are_consecutive_candles_of_the_rules_interval() -> None:
    facts = _facts(40)
    with pytest.raises(ValidationFailure, match="consecutive"):
        slot_verdicts(facts[:10] + facts[11:], RULES, history=30)
    with pytest.raises(ValidationFailure, match="consecutive"):
        slot_verdicts(_facts(40), _rules(interval_minutes=15), history=30)


def test_a_label_at_a_moment_uses_the_newest_candle_closed_by_then_within_the_lag() -> None:
    facts = _facts(40, missing={38, 39})
    timeline = MarketTypeTimeline(slot_verdicts(facts, RULES, history=30), RULES)
    on_time = timeline.at(T0 + HOUR * 37 + timedelta(minutes=5))
    assert (on_time.market_type, on_time.candle_close, on_time.lag_bars) == ("rising", T0 + HOUR * 37, 0)
    exactly = timeline.at(T0 + HOUR * 37)  # a candle that closed at the moment may be used
    assert exactly.candle_close == T0 + HOUR * 37
    before = timeline.at(T0 + HOUR * 37 - timedelta(seconds=1))
    assert before.candle_close == T0 + HOUR * 36  # never a candle that closed after the moment
    late = timeline.at(T0 + HOUR * 39 + timedelta(minutes=59))  # two newest candles missing: within the lag
    assert (late.market_type, late.candle_close, late.lag_bars) == ("rising", T0 + HOUR * 37, 2)
    stale = timeline.at(T0 + HOUR * 40)  # three intervals behind
    assert (stale.market_type, stale.reason, stale.candle_close, stale.lag_bars) == (
        "unclear", "no_recent_candle", T0 + HOUR * 37, 3)
    early = timeline.at(T0 - timedelta(minutes=1))
    assert (early.market_type, early.reason, early.candle_close) == ("unclear", "no_recent_candle", None)
    assert label_at(T0 + HOUR * 37, slot_verdicts(facts, RULES, history=30), RULES) == exactly


def test_a_label_document_names_its_rules_moment_and_inputs() -> None:
    timeline = MarketTypeTimeline(slot_verdicts(_facts(40), RULES, history=30), RULES)
    document = timeline.at(T0 + HOUR * 39 + timedelta(minutes=5)).to_document()
    assert document["market_type"] == "rising" and document["reason"] is None
    assert document["rules"] == {"ref": "market-type-rules@1", "sha256": RULES.content_sha256,
                                 "interval_minutes": 60}
    assert document["moment_utc"] == "2026-09-02T15:05:00.000000Z"
    assert document["candle_close_utc"] == "2026-09-02T15:00:00.000000Z" and document["lag_bars"] == 0
    assert document["checks"]["strength"] == "trending" and document["values"]["plus"] == "28"


def test_the_vocabulary_and_the_kernel_path() -> None:
    assert MARKET_TYPES == ("rising", "sideways", "falling", "unclear")
    assert set(UNCLEAR_REASONS) == {"no_recent_candle", "not_enough_candles", "gap", "no_value",
                                    "evaluation_failed"}
    for path in ("src/trade_graph/kernel/market_type.py", "src/trade_graph/kernel/trade_metrics.py",
                 "src/trade_graph/domain/market_types.py", "src/trade_graph/application/market_types/labeller.py",
                 "src/trade_graph/application/market_types/hourly.py",
                 "src/trade_graph/application/market_types/store.py",
                 "src/trade_graph/adapters/persistence/market_type_schema.py",
                 "migrations/versions/0050_market_type_labels.py"):
        assert path_is_protected(path), path
