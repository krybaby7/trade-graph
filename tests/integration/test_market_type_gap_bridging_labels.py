"""Version 2 labels through the candle store and the indicator engine (L2, D230): a short hole is skipped live and in
replays alike, and the indicators read the candles on both sides of it as if they followed each other.

A steady hourly rise with a 5-hour hole at candles 900 to 904. Version 1 makes every label for 863 hours after the hole
``unclear`` (``gap``); version 2 keeps the rise ``rising`` once a candle is back. Synthetic candles
(``synthetic_fixture``); no network.
"""

from __future__ import annotations

import pytest
from tests.market_type_support import (
    FIRST_LABELLED,
    HOUR,
    SERIES_START,
    closes_rising,
    hourly_moment,
    series,
    store_series,
)
from tests.practice_range_support import open_store

from trade_graph.application.market_types.labeller import label_coin, read_timeline, slot_facts
from trade_graph.kernel.market_type import MARKET_TYPE_RULES_V1, MARKET_TYPE_RULES_V2

SYMBOL = "SYN/USD"
HOLE = range(900, 905)
COUNT = 1200


@pytest.fixture
def candles(tmp_path):
    database, store = open_store(tmp_path / "market" / "market_data.sqlite")
    store_series(store, series(SYMBOL, closes_rising(COUNT), skip=set(HOLE)))
    yield store
    database.close()


def _timeline(store, rules):
    end = SERIES_START + HOUR * COUNT
    return read_timeline(store, SYMBOL, "synthetic", end_close=end, slots=COUNT - FIRST_LABELLED, as_of=end + HOUR,
                         rules=rules)


def test_version_two_keeps_a_rise_rising_across_a_five_hour_hole(candles) -> None:
    after = range(HOLE.stop, COUNT)
    two = _timeline(candles, MARKET_TYPE_RULES_V2)
    assert {two.at(hourly_moment(index)).market_type for index in after} == {"rising"}
    one = _timeline(candles, MARKET_TYPE_RULES_V1)
    # Version 1: every label after the hole is unclear until 863 complete hours follow it, beyond this series' end.
    assert {(one.at(hourly_moment(index)).market_type, one.at(hourly_moment(index)).reason)
            for index in after} == {("unclear", "gap")}


def test_during_the_hole_the_label_waits_for_a_recent_candle(candles) -> None:
    two = _timeline(candles, MARKET_TYPE_RULES_V2)
    # Up to 2 hours without a candle the last label stands (the lag rule, D285); then it is unclear until one is back.
    assert [two.at(hourly_moment(index)).market_type for index in (899, 900, 901)] == ["rising"] * 3
    assert {(two.at(hourly_moment(index)).market_type, two.at(hourly_moment(index)).reason)
            for index in (902, 903, 904)} == {("unclear", "no_recent_candle")}


def test_the_indicators_read_across_the_hole(candles) -> None:
    end = SERIES_START + HOUR * 1000
    window = candles.window(SYMBOL, 60, "synthetic", end_close_time=end, count=1000, as_of=end + HOUR,
                            mode="point_in_time")
    after_hole = HOLE.stop  # the window starts at candle 0, so the slot index is the candle index
    two = slot_facts(window, MARKET_TYPE_RULES_V2)[after_hole]
    one = slot_facts(window, MARKET_TYPE_RULES_V1)[after_hole]
    assert None not in (two.average, two.strength, two.plus, two.minus)
    assert (one.average, one.strength) == (None, None)  # version 1 withholds a gapped reading (D136)


@pytest.mark.parametrize("index", [HOLE.stop, HOLE.stop + 1, HOLE.stop + 30, HOLE.stop + 500, HOLE.stop + 880])
def test_a_live_label_equals_the_replays_label_after_the_hole(candles, index) -> None:
    moment = hourly_moment(index)
    live = label_coin(candles, symbol=SYMBOL, moment=moment, rules=MARKET_TYPE_RULES_V2, source="synthetic").label
    replay = _timeline(candles, MARKET_TYPE_RULES_V2).at(moment)
    assert (live.market_type, live.reason, live.candle_close, dict(live.checks), dict(live.values)) == (
        replay.market_type, replay.reason, replay.candle_close, dict(replay.checks), dict(replay.values))


def test_a_label_names_its_rules_and_the_candles_it_looked_back_over(candles) -> None:
    coin = label_coin(candles, symbol=SYMBOL, moment=hourly_moment(HOLE.stop + 10), source="synthetic")
    document = coin.to_document()
    assert document["rules"]["ref"] == "market-type-rules@2"
    assert document["candles"]["history_candles"] == 863
    assert document["candles"]["look_back_slots"] == 887
    assert document["candles"]["candles_read"] == 887 - len(HOLE)
