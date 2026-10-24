"""Rolling indicator values for replays (T29) equal the live engine's newest value at every candle.

The practice range asks, for each candle of a long window, what the engine would have answered with ``count=1`` at
that candle's close. ``rolling_latest`` computes that in one pass over one window read; these tests check it
against ``compute_indicator`` itself, float for float, including the candles a gap withholds.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.indicator_series import build_candles

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.engine import compute_indicator, plan_history
from trade_graph.application.indicators.rolling import rolling_latest
from trade_graph.domain.errors import ValidationFailure

COUNT = 1_900
GAP = 1_400
CASES = [
    ("SMA", {"timeperiod": 20}, None),
    ("EMA", {"timeperiod": 30}, None),
    ("RSI", {"timeperiod": 14}, None),
    ("MACD", {}, "histogram"),
    ("BBANDS", {"timeperiod": 20}, "lower"),
    ("HIGHEST_HIGH", {"timeperiod": 20}, None),
    ("ADX", {"timeperiod": 14}, None),
]


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    database = MarketDataDatabase(tmp_path_factory.mktemp("rolling") / "market_data.sqlite")
    store = CandleStore(database)
    candles = [candle for index, candle in enumerate(build_candles("trend_pullback", count=COUNT)) if index != GAP]
    run_id = store.begin_run(kind="public_collect", source="synthetic", evidence_label="synthetic_fixture",
                             started_at=candles[-1].available_at_utc, parameters={"test": "rolling"})
    store.save(candles, run_id=run_id)
    end = candles[-1].close_time_utc
    as_of = candles[-1].available_at_utc
    window = store.window("SYN/USD", 15, "synthetic", end_close_time=end, count=COUNT, as_of=as_of)
    yield store, window, as_of
    database.close()


@pytest.mark.parametrize(("name", "params", "output"), CASES)
def test_every_rolling_value_is_the_engines_newest_value_at_that_candle(stored, name, params, output) -> None:
    store, window, as_of = stored
    rolling = rolling_latest(name, params, window)
    span = plan_history(name, params, 1).span
    assert rolling.span == span
    sample = sorted(index for index in {span - 2, span - 1, span, GAP - 1, GAP, GAP + span - 1, GAP + span,
                                        COUNT - 1, *range(span, COUNT, 97)} if 0 <= index < COUNT)
    assert len(sample) > 5
    for index in sample:
        live = compute_indicator(store, name, params, symbol="SYN/USD", interval_minutes=15, source="synthetic",
                                 end_close_time=window.slots[index].close_time_utc, as_of=as_of)
        expected = live.latest(output) if live.is_ready else None
        assert rolling.at(index, output) == expected, (name, index, live.status)


def test_a_gap_withholds_exactly_the_candles_whose_span_contains_it(stored) -> None:
    _, window, _ = stored
    rolling = rolling_latest("SMA", {"timeperiod": 20}, window)
    span = rolling.span
    assert rolling.at(GAP - 1) is not None
    assert all(rolling.at(index) is None for index in range(GAP, GAP + span))
    assert rolling.at(GAP + span) is not None
    assert rolling.unready_count() == (span - 1) + span


def test_values_are_labelled_with_the_menu_entry_and_library(stored) -> None:
    _, window, _ = stored
    rolling = rolling_latest("RSI", {"timeperiod": 14}, window)
    assert rolling.spec_version.startswith("indicator-menu-v1/")
    assert set(rolling.library) == {"ta_lib_python", "ta_lib_c", "numpy"}
    assert rolling.close_times[0] == window.slots[0].close_time_utc
    assert rolling.close_times[-1] - rolling.close_times[-2] == timedelta(minutes=15)


def test_a_multi_output_indicator_needs_its_output_named(stored) -> None:
    _, window, _ = stored
    rolling = rolling_latest("MACD", {}, window)
    with pytest.raises(ValidationFailure, match="name one"):
        rolling.at(COUNT - 1)


def test_menu_settings_are_checked_before_anything_is_computed(stored) -> None:
    _, window, _ = stored
    with pytest.raises(ValidationFailure):
        rolling_latest("SMA", {"timeperiod": 0}, window)
    with pytest.raises(ValidationFailure):
        rolling_latest("NOT_ON_MENU", {}, window)
