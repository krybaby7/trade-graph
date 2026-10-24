"""Golden market-type labels on synthetic hourly series (W11-A, C10; D285, D286).

A steady rise, a steady fall, a flat range and a gap, each read through the candle store and the indicator engine
exactly as the hourly scan reads it, give the expected label from the expected first candle: candle 862, the first
whose 863 hourly candles (ADX 14's span, D136) are all present. Candles are synthetic (``synthetic_fixture``); no
network.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.market_type_support import (
    FIRST_LABELLED,
    HOUR,
    SERIES_START,
    closes_falling,
    closes_range,
    closes_rising,
    hourly_moment,
    series,
    store_series,
)
from tests.practice_range_support import open_store

from trade_graph.application.indicators.engine import plan_history
from trade_graph.application.market_types.labeller import (
    LIVE_SOURCE,
    label_coin,
    read_timeline,
    required_history,
)
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.market_type import MARKET_TYPE_RULES_V1

SYMBOL = "SYN/USD"
RULES = MARKET_TYPE_RULES_V1


@pytest.fixture
def candles(tmp_path):
    database, store = open_store(tmp_path / "market" / "market_data.sqlite")
    yield store
    database.close()


def _labels(store, count: int) -> list[str]:
    """The label 5 minutes after each candle closed, read from one timeline over the whole series."""
    end = SERIES_START + HOUR * count
    timeline = read_timeline(store, SYMBOL, "synthetic", end_close=end, slots=count, as_of=end + HOUR, rules=RULES)
    return [timeline.at(hourly_moment(index)).market_type for index in range(count)]


def test_the_history_is_adx_fourteens_span() -> None:
    assert required_history(RULES) == plan_history("ADX", {"timeperiod": 14}, 1).span == FIRST_LABELLED + 1 == 863
    assert required_history(RULES) > plan_history("SMA", {"timeperiod": 168}, 1).span + RULES.slope_bars
    assert LIVE_SOURCE == "kraken_public_ohlc"


def test_a_steady_rise_is_rising_from_the_first_complete_history(candles) -> None:
    store_series(candles, series(SYMBOL, closes_rising(1000)))
    assert _labels(candles, 1000) == ["unclear"] * FIRST_LABELLED + ["rising"] * (1000 - FIRST_LABELLED)
    first = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(FIRST_LABELLED), source="synthetic",
                       rules=RULES)
    assert first.label.market_type == "rising" and first.label.lag_bars == 0
    assert first.label.checks == {"strength": "trending", "pressure": "buyers", "price": "above_average",
                                  "average": "rising"}
    before = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(FIRST_LABELLED - 1), source="synthetic",
                       rules=RULES)
    assert (before.label.market_type, before.label.reason) == ("unclear", "not_enough_candles")


def test_a_steady_fall_is_falling_from_the_first_complete_history(candles) -> None:
    store_series(candles, series(SYMBOL, closes_falling(1000)))
    assert _labels(candles, 1000) == ["unclear"] * FIRST_LABELLED + ["falling"] * (1000 - FIRST_LABELLED)
    label = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(999), source="synthetic",
                       rules=RULES).label
    assert label.checks == {"strength": "trending", "pressure": "sellers", "price": "below_average",
                            "average": "falling"}


def test_a_flat_range_is_sideways_from_the_first_complete_history(candles) -> None:
    store_series(candles, series(SYMBOL, closes_range(1000)))
    assert _labels(candles, 1000) == ["unclear"] * FIRST_LABELLED + ["sideways"] * (1000 - FIRST_LABELLED)
    label = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(999), source="synthetic",
                       rules=RULES).label
    assert label.checks["average"] == "flat" and label.checks["strength"] == "weak"


def test_a_gap_keeps_the_last_label_within_the_lag_then_withholds_it_for_one_history(candles) -> None:
    store_series(candles, series(SYMBOL, closes_rising(1900), skip={1000}))
    labels = _labels(candles, 1900)
    assert labels[:FIRST_LABELLED] == ["unclear"] * FIRST_LABELLED
    assert labels[FIRST_LABELLED:1000] == ["rising"] * (1000 - FIRST_LABELLED)
    assert labels[1000] == "rising"  # the missing hour: the candle before it, one interval behind
    assert labels[1001:1863] == ["unclear"] * 862  # every history that holds the missing hour
    assert labels[1863:] == ["rising"] * (1900 - 1863)
    during = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(1000), source="synthetic",
                       rules=RULES).label
    assert (during.candle_close, during.lag_bars) == (SERIES_START + HOUR * 1000, 1)
    gap = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(1001), source="synthetic",
                       rules=RULES).label
    assert (gap.market_type, gap.reason) == ("unclear", "gap")
    oldest = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(1862), source="synthetic",
                       rules=RULES).label
    assert (oldest.market_type, oldest.reason) == ("unclear", "not_enough_candles")


def test_too_few_candles_and_no_recent_candle_are_unclear(candles) -> None:
    store_series(candles, series(SYMBOL, closes_rising(500)))
    young = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(499), source="synthetic",
                       rules=RULES).label
    assert (young.market_type, young.reason) == ("unclear", "not_enough_candles")
    store_series(candles, series("OLD/USD", closes_rising(900)))
    late = label_coin(candles, symbol="OLD/USD", moment=hourly_moment(899) + 2 * HOUR, source="synthetic",
                       rules=RULES).label
    assert (late.market_type, late.lag_bars) == ("rising", 2)
    stale = label_coin(candles, symbol="OLD/USD", moment=hourly_moment(899) + 3 * HOUR, source="synthetic",
                       rules=RULES).label
    assert (stale.market_type, stale.reason, stale.lag_bars) == ("unclear", "no_recent_candle", 3)
    nothing = label_coin(candles, symbol="NONE/USD", moment=hourly_moment(899), source="synthetic",
                       rules=RULES).label
    assert (nothing.market_type, nothing.reason, nothing.candle_close) == ("unclear", "no_recent_candle", None)


def test_a_live_label_never_reads_a_candle_received_after_its_moment(candles) -> None:
    rows = series(SYMBOL, closes_rising(901))
    late = rows[900].model_copy(update={"available_at_utc": rows[900].close_time_utc + 2 * HOUR})
    store_series(candles, rows[:900])
    store_series(candles, [late], at=late.available_at_utc)
    moment = hourly_moment(900)  # five minutes after candle 900 closed, before it arrived
    label = label_coin(candles, symbol=SYMBOL, moment=moment, source="synthetic",
                       rules=RULES)
    assert (label.label.candle_close, label.label.lag_bars) == (SERIES_START + HOUR * 900, 1)
    after = label_coin(candles, symbol=SYMBOL, moment=late.available_at_utc, source="synthetic",
                       rules=RULES)
    assert after.label.candle_close == late.close_time_utc and after.label.lag_bars == 2


def test_a_live_label_keeps_its_inputs_and_rules(candles) -> None:
    run_id = store_series(candles, series(SYMBOL, closes_rising(900)))
    coin = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(899), source="synthetic",
                       rules=RULES)
    document = coin.to_document()
    assert document["symbol"] == SYMBOL and document["market_type"] == "rising"
    assert document["rules"] == {"ref": "market-type-rules@1", "sha256": RULES.content_sha256,
                                 "interval_minutes": 60}
    assert document["candles"]["source"] == "synthetic" and document["candles"]["read_mode"] == "point_in_time"
    assert document["candles"]["run_ids"] == [run_id] and document["candles"]["evidence_labels"] == [
        "synthetic_fixture"]
    assert document["candles"]["candles_read"] == 863 and len(document["candles"]["input_sha256"]) == 64
    assert set(document["indicators"]) == {"average", "strength", "plus", "minus"}
    assert document["indicators"]["strength"]["indicator"] == "ADX"
    assert coin.evidence == "synthetic_fixture"
    assert "rising" in coin.sentence() and "ADX" in coin.sentence()


def test_a_moment_must_be_aware_and_the_rules_interval_supported(candles) -> None:
    with pytest.raises(ValidationFailure):
        label_coin(candles, symbol=SYMBOL, moment=hourly_moment(1).replace(tzinfo=None), source="synthetic",
                       rules=RULES)
    with pytest.raises(ValidationFailure):
        read_timeline(candles, SYMBOL, "synthetic", end_close=SERIES_START + timedelta(minutes=30), slots=10,
                      as_of=SERIES_START + HOUR, rules=RULES)
