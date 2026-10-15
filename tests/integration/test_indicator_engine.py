"""The indicator engine over synthetic candle stores: readiness, gaps, modes, provenance and bounds. No network."""

from __future__ import annotations

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.indicator_series import build_candles

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators import engine
from trade_graph.application.indicators.engine import (
    MAX_HISTORY_CANDLES,
    MAX_OUTPUT_POINTS,
    START_DEPENDENT_HISTORY,
    CandleSelectionError,
    compare_to_level,
    compute_from_window,
    compute_indicator,
    plan_history,
)
from trade_graph.application.indicators.library import compute_raw, library_versions, lookback
from trade_graph.contracts.candles import Candle, CandleSlot, CandleWindow, StoredCandle
from trade_graph.contracts.indicators import INDICATOR_MENU, get_indicator
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure

_ENGINE_SOURCE = Path(engine.__file__)
_SYMBOL, _SOURCE = "SYN/USD", "synthetic"


@pytest.fixture
def store(tmp_path):
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    yield CandleStore(database)
    database.close()


def _run(store: CandleStore, at: datetime) -> int:
    return store.begin_run(kind="public_collect", source=_SOURCE, evidence_label="synthetic_fixture",
                           started_at=at, parameters={"note": "engine test"})


def _fill(store: CandleStore, *, interval: int = 15, count: int = 200, skip: tuple[int, ...] = (),
          series: str = "trend_pullback") -> list[Candle]:
    candles = build_candles(series, interval_minutes=interval, count=count)
    kept = [candle for index, candle in enumerate(candles) if index not in skip]
    store.save(kept, run_id=_run(store, candles[-1].available_at_utc + timedelta(hours=1)))
    return candles


def _end(candles: list[Candle], index: int) -> datetime:
    return candles[index].close_time_utc


def _read(store: CandleStore, candles: list[Candle], index: int, name: str = "SMA", params=None, *,
          count: int = 1, interval: int = 15, mode="point_in_time", as_of: datetime | None = None):
    end = _end(candles, index)
    return compute_indicator(store, name, params or {}, symbol=_SYMBOL, interval_minutes=interval, source=_SOURCE,
                             end_close_time=end, as_of=as_of or end + timedelta(minutes=1), count=count, mode=mode)


class SpyStore(CandleStore):
    """Records every window call so tests can see what the engine asked the contract for."""

    def __init__(self, database) -> None:
        super().__init__(database)
        self.calls: list[dict] = []

    def window(self, symbol, interval_minutes, source, **keywords):
        self.calls.append({"symbol": symbol, "interval_minutes": interval_minutes, "source": source, **keywords})
        return super().window(symbol, interval_minutes, source, **keywords)


# --- ready ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("interval", [1, 5, 15, 60])
def test_ready_values_are_the_librarys_values_on_exactly_the_candles_read(store, interval) -> None:
    candles = _fill(store, interval=interval, count=120)
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20}, count=3, interval=interval)
    assert result.status == "ready" and result.missing_close_times_utc == ()
    assert result.interval_minutes == interval and result.requested_points == 3
    used = candles[80:100]  # 20 candles end the newest point; 2 more points need 2 more before them
    columns = {"close": tuple(c.close for c in candles[78:100])}
    expected = compute_raw("SMA", {"timeperiod": 20}, columns)["value"][-3:]
    assert result.values["value"] == tuple(float(x) for x in expected)
    assert all(type(x) is float for x in result.values["value"])
    assert result.close_times_utc == tuple(c.close_time_utc for c in candles[97:100])
    assert result.close_times_utc[0] < result.close_times_utc[-1]
    assert result.required_candles == 22 and result.available_candles == 22 and len(used) == 20
    assert result.latest() == result.values["value"][-1]


def test_multi_output_indicators_return_every_output_in_step(store) -> None:
    candles = _fill(store, count=800)
    result = _read(store, candles, 790, "MACD", {}, count=4)
    assert result.status == "ready"
    assert set(result.values) == {"macd", "signal", "histogram"}
    assert all(len(series) == 4 for series in result.values.values())
    assert all(math.isfinite(x) for series in result.values.values() for x in series)
    assert result.close_times_utc == tuple(c.close_time_utc for c in candles[787:791])
    with pytest.raises(ValidationFailure):
        result.latest()  # more than one output: the caller must name it
    assert result.latest("histogram") == result.values["histogram"][-1]


def test_start_sensitive_indicators_read_the_same_span_whatever_the_number_of_points(store) -> None:
    candles = _fill(store, count=1200)
    spec = get_indicator("RSI")
    guidance = spec.settle.periods * 14
    assert guidance == 280
    for points in (1, 2, 60, MAX_OUTPUT_POINTS):
        plan = plan_history("RSI", {}, points)
        assert plan.lookback == lookback("RSI", {}) == 14
        # The measured guidance, plus the points not asked for: the span always starts at the same candle.
        assert plan.seeding == guidance + MAX_OUTPUT_POINTS - points
        assert plan.span == 14 + guidance + MAX_OUTPUT_POINTS == 794
    result = _read(store, candles, 1100, "RSI", {})
    assert (result.lookback_candles, result.seeding_candles, result.required_candles) == (14, 280 + 499, 794)
    assert result.start_dependent is True


def test_entries_that_never_forget_their_start_use_the_fixed_history_and_say_so(store) -> None:
    for name in ("SAR", "OBV"):
        spec = get_indicator(name)
        assert spec.recursive_seed and spec.settle is None
        plan = plan_history(name, {}, MAX_OUTPUT_POINTS)
        assert plan.seeding == START_DEPENDENT_HISTORY == 200
        assert plan_history(name, {}, 1).span == plan.span == plan.lookback + 200 + MAX_OUTPUT_POINTS
    candles = _fill(store, count=1000)
    assert _read(store, candles, 999, "OBV", {}).start_dependent is True
    assert _read(store, candles, 999, "SAR", {}).start_dependent is True


def test_windowed_indicators_need_only_their_own_window_and_are_not_start_dependent(store) -> None:
    candles = _fill(store, count=100)
    result = _read(store, candles, 40, "BBANDS", {"timeperiod": 10}, count=2)
    assert (result.seeding_candles, result.required_candles, result.start_dependent) == (0, 11, False)
    assert result.status == "ready"


@pytest.mark.parametrize("name", [spec.name for spec in INDICATOR_MENU])
def test_the_same_end_candle_gives_the_same_values_whatever_the_number_of_points(store, name) -> None:
    """The value at a candle is a function of that candle, not of how many neighbours were asked for with it.

    Start-sensitive entries read the same span for every count, so their numbers are identical bit for bit.
    Window entries need nothing before their window; the library's running sums may still differ in the last
    one or two digits when the window starts elsewhere, which the 12-digit comparison rule in compare_to_level absorbs.
    """
    candles = _fill(store, count=1300)
    spec = get_indicator(name)
    reference = _read(store, candles, 1250, name, {}, count=60)
    assert reference.status == "ready"
    for count in (1, 2, 3, 17):
        other = _read(store, candles, 1250, name, {}, count=count)
        assert other.status == "ready", (name, count)
        for output, series in other.values.items():
            expected = reference.values[output][-count:]
            if spec.recursive_seed:
                assert series == expected, (name, output, count)  # bit for bit
            else:
                assert series == pytest.approx(expected, rel=1e-12, abs=1e-9), (name, output, count)


def test_an_alarm_level_cannot_flip_between_one_point_and_many_for_the_same_candle(store) -> None:
    """When the span followed `count`, the newest MACD histogram moved by 0.03 percent between count 1 and count 2."""
    candles = _fill(store, count=1300)

    def followed_count(points: int) -> float:  # what the engine read when the span was lookback + 4 x slow + count
        span = lookback("MACD", {}) + 4 * 26 + points
        closes = {"close": tuple(c.close for c in candles[1251 - span : 1251])}
        return float(compute_raw("MACD", {}, closes)["histogram"][-1])

    one, two = followed_count(1), followed_count(2)
    assert abs(one - two) > 1e-6 * abs(one), "the fixture must show the two spans disagreeing"
    level = Decimal(repr((one + two) / 2))  # a level that the one-point and the two-point reading sat either side of
    answers = set()
    for count in (1, 2, 3, 60):
        result = _read(store, candles, 1250, "MACD", {}, count=count)
        answers.add((compare_to_level(result, level, "above", output="histogram").satisfied,
                     compare_to_level(result, level, "below", output="histogram").satisfied))
    assert len(answers) == 1, answers


def test_each_menu_entry_at_its_defaults_is_ready_on_a_long_enough_history(store) -> None:
    candles = _fill(store, count=1300)  # the longest span at the defaults is ADX: 27 + 336 + 500 = 863 candles
    for spec in INDICATOR_MENU:
        result = _read(store, candles, 1299, spec.name, {}, count=2)
        assert result.status == "ready", spec.name
        assert set(result.values) == {output.name for output in spec.outputs}
        assert result.spec_version.startswith("indicator-menu-v") and "/" in result.spec_version


# --- warming up and gaps -----------------------------------------------------------------------------------------


def test_not_enough_history_is_warming_up_and_withholds_values(store) -> None:
    candles = _fill(store, count=10)
    result = _read(store, candles, 9, "SMA", {"timeperiod": 20})
    assert result.status == "warming_up"
    assert result.values == {} and result.close_times_utc == ()
    assert result.available_candles == 10 and result.required_candles == 20
    assert len(result.missing_close_times_utc) == 10
    assert result.missing_close_times_utc[-1] == _end(candles, 9) - timedelta(minutes=15 * 10)


def test_a_series_that_does_not_exist_is_gapped_not_a_young_coin(store) -> None:
    """Nothing was seen in the span, so nothing shows that the history has not begun: it is a data problem."""
    end = datetime(2026, 2, 1, tzinfo=UTC)
    result = compute_indicator(store, "RSI", {}, symbol="ABC/USD", interval_minutes=15, source=_SOURCE,
                               end_close_time=end, as_of=end, count=1)
    assert result.status == "gapped" and result.available_candles == 0
    assert len(result.missing_close_times_utc) == result.required_candles
    assert result.values == {}


@pytest.mark.parametrize("silent_slots", [5, 20, 40, 1000, 5000])
def test_a_feed_that_stopped_long_ago_is_gapped_whatever_the_distance_never_a_young_coin(store, silent_slots) -> None:
    candles = _fill(store, count=100)
    end = _end(candles, 99) + timedelta(minutes=15 * silent_slots)
    result = compute_indicator(store, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                               end_close_time=end, as_of=end, count=1)
    assert result.status == "gapped" and result.values == {}
    assert result.available_candles == max(0, 20 - silent_slots)
    assert len(result.missing_close_times_utc) == min(20, silent_slots)


def test_a_span_with_no_candle_in_it_does_not_read_the_look_behind(tmp_path) -> None:
    database = MarketDataDatabase(tmp_path / "spy3" / "market_data.sqlite")
    spy = SpyStore(database)
    candles = _fill(spy, count=100)
    spy.calls.clear()
    end = _end(candles, 99) + timedelta(minutes=15 * 500)
    result = compute_indicator(spy, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                               end_close_time=end, as_of=end, count=1)
    assert result.status == "gapped" and len(spy.calls) == 1
    database.close()


def test_missing_oldest_slots_with_nothing_stored_before_them_are_warming_up(store) -> None:
    candles = _fill(store, count=120, skip=tuple(range(85)))  # the history begins at candle 85
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20})  # the span starts at candle 80
    assert result.status == "warming_up" and result.available_candles == 15
    assert result.missing_close_times_utc == tuple(_end(candles, index) for index in range(80, 85))


def test_missing_oldest_slots_with_candles_stored_before_them_are_a_gap_not_a_young_coin(store) -> None:
    candles = _fill(store, count=120, skip=tuple(range(71, 85)))  # candles up to 70 exist, then a hole to 84
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20})
    assert result.status == "gapped" and result.values == {}
    assert result.missing_close_times_utc == tuple(_end(candles, index) for index in range(80, 85))


def test_the_look_behind_is_one_more_window_read_and_only_when_the_answer_needs_it(tmp_path) -> None:
    database = MarketDataDatabase(tmp_path / "spy2" / "market_data.sqlite")
    spy = SpyStore(database)
    candles = _fill(spy, count=120, skip=(80,))
    ready = _read(spy, candles, 119, "SMA", {"timeperiod": 20})
    assert ready.status == "ready" and len(spy.calls) == 1  # a complete answer reads once
    spy.calls.clear()
    result = _read(spy, candles, 99, "SMA", {"timeperiod": 20})  # the oldest slot of the span is missing
    assert result.status == "gapped" and len(spy.calls) == 2
    first, second = spy.calls
    assert first["count"] == second["count"] == 20
    assert second["end_close_time"] == _end(candles, 79)  # the slot just before the span, same length
    assert (second["mode"], second["as_of"]) == (first["mode"], first["as_of"])
    database.close()


@pytest.mark.parametrize("name", [spec.name for spec in INDICATOR_MENU])
def test_every_menu_entry_reports_a_hole_inside_its_own_span_and_only_there(tmp_path, name) -> None:
    plan = plan_history(name, {}, 2)
    hole = plan.span  # far enough from the start that every span ending after it lies inside the stored series
    database = MarketDataDatabase(tmp_path / name / "market_data.sqlite")
    holed = CandleStore(database)
    candles = _fill(holed, count=hole + plan.span + 1, skip=(hole,))
    blocked = _read(holed, candles, hole + plan.span // 2, name, {}, count=2)  # the span contains the hole
    assert blocked.status == "gapped" and blocked.values == {} and blocked.close_times_utc == ()
    assert blocked.missing_close_times_utc == (_end(candles, hole),)
    assert blocked.required_candles == plan.span and blocked.available_candles == plan.span - 1
    clear = _read(holed, candles, hole + plan.span, name, {}, count=2)  # the span starts one slot after the hole
    assert clear.status == "ready" and set(clear.values) == {o.name for o in get_indicator(name).outputs}
    database.close()


def test_a_gap_in_the_middle_withholds_the_values_and_names_the_slot(store) -> None:
    candles = _fill(store, count=120, skip=(90,))
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20})
    assert result.status == "gapped"
    assert result.values == {} and result.close_times_utc == ()
    assert result.missing_close_times_utc == (_end(candles, 90),)
    assert result.available_candles == 19


def test_a_gap_does_not_poison_a_window_that_does_not_contain_it(store) -> None:
    candles = _fill(store, count=120, skip=(50,))
    after = _read(store, candles, 99, "SMA", {"timeperiod": 20})  # span 80..99
    before_the_gap_matters = _read(store, candles, 60, "SMA", {"timeperiod": 20})  # span 41..60 contains 50
    assert after.status == "ready"
    assert before_the_gap_matters.status == "gapped"
    assert before_the_gap_matters.missing_close_times_utc == (_end(candles, 50),)


def test_a_gap_in_the_warm_up_zone_still_withholds_values(store) -> None:
    candles = _fill(store, count=120, skip=(80,))
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20})  # slot 80 is the oldest candle the average needs
    assert result.status == "gapped" and result.missing_close_times_utc == (_end(candles, 80),)


def test_a_gap_before_the_first_output_point_withholds_every_point_not_a_partial_answer(store) -> None:
    candles = _fill(store, count=120, skip=(78,))
    # Three points ask for two more candles of history; slot 78 only feeds the first point's window.
    result = _read(store, candles, 99, "SMA", {"timeperiod": 20}, count=3)
    assert result.status == "gapped" and result.missing_close_times_utc == (_end(candles, 78),)
    assert result.values == {} and result.close_times_utc == ()


def test_a_gap_in_the_seeding_history_of_a_recursive_indicator_withholds_values(store) -> None:
    candles = _fill(store, count=900, skip=(100,))
    span = 9 + get_indicator("EMA").settle.periods * 10 + MAX_OUTPUT_POINTS  # EMA(10): lookback, guidance, padding
    ok = _read(store, candles, 899, "EMA", {"timeperiod": 10})  # the span starts well after the missing candle
    assert ok.status == "ready" and ok.required_candles == span
    # Ending at 600 the span reaches back past the missing candle, which lies in the seeding part, not the warm-up.
    result = _read(store, candles, 600, "EMA", {"timeperiod": 10})
    assert result.status == "gapped" and result.missing_close_times_utc == (_end(candles, 100),)
    assert 600 - 100 > result.lookback_candles + result.requested_points  # the gap is older than the warm-up zone


def test_a_gap_at_the_newest_slot_is_gapped_and_the_lookback_is_not_lengthened(tmp_path) -> None:
    database = MarketDataDatabase(tmp_path / "spy" / "market_data.sqlite")
    spy = SpyStore(database)
    candles = _fill(spy, count=120, skip=(99,))
    end = _end(candles, 99)
    result = compute_indicator(spy, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                               end_close_time=end, as_of=end + timedelta(minutes=1), count=1)
    assert result.status == "gapped" and result.missing_close_times_utc == (end,)
    assert result.values == {}
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["count"] == 20 and call["end_close_time"] == end  # the span is exactly the average's window
    assert call["mode"] == "point_in_time"
    database.close()


def test_a_collector_that_stopped_leaves_trailing_gaps_not_a_shorter_answer(store) -> None:
    candles = _fill(store, count=100)
    end = _end(candles, 99) + timedelta(minutes=45)  # three slots after the last stored candle
    result = compute_indicator(store, "SMA", {"timeperiod": 5}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                               end_close_time=end, as_of=end, count=1)
    assert result.status == "gapped"
    assert result.missing_close_times_utc == tuple(end - timedelta(minutes=15 * k) for k in (2, 1, 0))


# --- visibility ------------------------------------------------------------------------------------------------


def test_a_candle_received_after_as_of_is_invisible_in_point_in_time_mode(store) -> None:
    candles = _fill(store, count=120)
    end = _end(candles, 99)
    received = candles[99].available_at_utc
    early = compute_indicator(store, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                              end_close_time=end, as_of=received - timedelta(seconds=1), count=1)
    assert early.status == "gapped" and early.missing_close_times_utc == (end,)
    on_time = compute_indicator(store, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15,
                                source=_SOURCE, end_close_time=end, as_of=received, count=1)
    assert on_time.status == "ready"


def test_a_revision_received_after_as_of_is_invisible_and_provenance_names_the_revision_used(store) -> None:
    candles = _fill(store, count=60)
    late = candles[59].model_copy(update={
        "close": candles[59].close + Decimal("1"), "high": candles[59].high + Decimal("1"),
        "available_at_utc": candles[59].available_at_utc + timedelta(hours=2)})
    second_run = store.begin_run(kind="public_collect", source=_SOURCE, evidence_label="synthetic_fixture",
                                 started_at=late.available_at_utc, parameters={"note": "correction"})
    store.save([late], run_id=second_run)
    end = _end(candles, 59)
    before = _read(store, candles, 59, "SMA", {"timeperiod": 5})
    after = _read(store, candles, 59, "SMA", {"timeperiod": 5}, as_of=late.available_at_utc + timedelta(seconds=1))
    assert after.latest() == pytest.approx(before.latest() + 1 / 5, abs=1e-6)
    assert [use.revision for use in before.candles_used][-1] == 1
    assert [use.revision for use in after.candles_used][-1] == 2
    assert after.candles_used[-1].run_id == second_run and before.candles_used[-1].run_id != second_run
    assert after.candles_used[-1].close_time_utc == end
    assert after.input_sha256 != before.input_sha256
    assert before.input_sha256 == _read(store, candles, 59, "SMA", {"timeperiod": 5}).input_sha256


def test_replay_mode_ignores_receipt_time_and_is_labelled(store) -> None:
    candles = _fill(store, count=120)
    end = _end(candles, 99)  # every candle was received 3 seconds after its close, so none had arrived at "end"
    replay = compute_indicator(store, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15,
                               source=_SOURCE, end_close_time=end, as_of=end, count=1, mode="replay")
    assert replay.status == "ready" and replay.mode == "replay"
    assert replay.availability_basis == "event_time_only"
    assert replay.to_document()["mode"] == "replay"
    assert replay.to_document()["availability_basis"] == "event_time_only"
    live = compute_indicator(store, "SMA", {"timeperiod": 20}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                             end_close_time=end, as_of=end, count=1)
    assert live.mode == "point_in_time" and live.availability_basis == "receipt_time"
    assert live.status == "gapped" and live.missing_close_times_utc == (end,)


def test_the_default_mode_is_point_in_time_and_it_requires_as_of(store) -> None:
    candles = _fill(store, count=40)
    with pytest.raises(ValidationFailure):
        compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                          end_close_time=_end(candles, 30), as_of=None, count=1)


def test_a_window_ending_after_as_of_is_refused_by_the_store_not_hidden(store) -> None:
    candles = _fill(store, count=40)
    end = _end(candles, 30)
    with pytest.raises(ValidationFailure):
        compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                          end_close_time=end, as_of=end - timedelta(minutes=1), count=1)


# --- the completed-candle guard ---------------------------------------------------------------------------------


def _hand_window(candles: list[Candle], *, as_of: datetime, mode="point_in_time") -> CandleWindow:
    slots = tuple(CandleSlot(close_time_utc=c.close_time_utc, stored=StoredCandle(candle=c, revision=1, run_id=1))
                  for c in candles)
    return CandleWindow(symbol=_SYMBOL, interval_minutes=15, source=_SOURCE, as_of_utc=as_of, mode=mode, slots=slots)


def test_the_engine_refuses_a_window_that_contains_a_candle_not_yet_closed_at_as_of() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    window = _hand_window(candles, as_of=candles[-1].close_time_utc - timedelta(minutes=1))
    with pytest.raises(CandleSelectionError):
        compute_from_window("SMA", {"timeperiod": 20}, window, count=1)


def test_the_engine_refuses_a_replay_window_with_a_candle_that_closes_after_as_of() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    window = _hand_window(candles, as_of=candles[-1].close_time_utc - timedelta(minutes=1), mode="replay")
    with pytest.raises(CandleSelectionError, match="closes after as_of"):
        compute_from_window("SMA", {"timeperiod": 20}, window, count=1)


def test_the_engine_refuses_a_point_in_time_window_with_a_candle_received_after_as_of() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    window = _hand_window(candles, as_of=candles[-1].close_time_utc + timedelta(seconds=1))  # receipt is +3 s
    with pytest.raises(CandleSelectionError):
        compute_from_window("SMA", {"timeperiod": 20}, window, count=1)


def test_the_engine_refuses_a_window_of_the_wrong_length() -> None:
    candles = build_candles("trend_pullback", count=30)[-19:]
    window = _hand_window(candles, as_of=candles[-1].available_at_utc)
    with pytest.raises(ValidationFailure):
        compute_from_window("SMA", {"timeperiod": 20}, window, count=1)


def test_a_hand_built_complete_window_computes() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    window = _hand_window(candles, as_of=candles[-1].available_at_utc)
    result = compute_from_window("SMA", {"timeperiod": 20}, window, count=1)
    assert result.status == "ready" and result.mode == "point_in_time"


def test_the_engine_refuses_a_window_whose_slots_are_not_consecutive() -> None:
    """compute_from_window is public: a hole between slots is not computed across because every slot is full."""
    candles = build_candles("trend_pullback", count=40)
    holed = candles[0:10] + candles[20:30]  # 20 slots, every one stored, ten slots missing between the halves
    window = _hand_window(holed, as_of=candles[29].available_at_utc)
    with pytest.raises(CandleSelectionError, match="consecutive"):
        compute_from_window("SMA", {"timeperiod": 20}, window, count=1)
    reversed_window = _hand_window(list(reversed(candles[10:30])), as_of=candles[29].available_at_utc)
    with pytest.raises(CandleSelectionError, match="consecutive"):
        compute_from_window("SMA", {"timeperiod": 20}, reversed_window, count=1)


def test_the_engine_refuses_candles_of_another_symbol_or_source_than_the_window() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    as_of = candles[-1].available_at_utc
    foreign = [c.model_copy(update={"symbol": "OTHER/USD"}) if index < 10 else c for index, c in enumerate(candles)]
    with pytest.raises(CandleSelectionError, match="symbol"):
        compute_from_window("SMA", {"timeperiod": 20}, _hand_window(foreign, as_of=as_of), count=1)
    elsewhere = [c.model_copy(update={"source": "kraken_public_ohlc"}) if index == 3 else c
                 for index, c in enumerate(candles)]
    with pytest.raises(CandleSelectionError, match="source"):
        compute_from_window("SMA", {"timeperiod": 20}, _hand_window(elsewhere, as_of=as_of), count=1)


def test_the_engine_refuses_a_candle_that_belongs_to_another_slot_or_interval() -> None:
    candles = build_candles("trend_pullback", count=30)[-20:]
    as_of = candles[-1].available_at_utc
    window = _hand_window(candles, as_of=as_of)
    swapped = list(window.slots)
    swapped[4], swapped[5] = (CandleSlot(close_time_utc=swapped[4].close_time_utc, stored=swapped[5].stored),
                              CandleSlot(close_time_utc=swapped[5].close_time_utc, stored=swapped[4].stored))
    with pytest.raises(CandleSelectionError, match="slot"):
        compute_from_window("SMA", {"timeperiod": 20}, CandleWindow(
            symbol=_SYMBOL, interval_minutes=15, source=_SOURCE, as_of_utc=as_of, mode="point_in_time",
            slots=tuple(swapped)), count=1)
    other_interval = [c.model_copy(update={"interval_minutes": 5}) if index == 7 else c
                      for index, c in enumerate(candles)]
    with pytest.raises(CandleSelectionError, match="slot"):
        compute_from_window("SMA", {"timeperiod": 20}, _hand_window(other_interval, as_of=as_of), count=1)


# --- bounds and refusals ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, -1, True, 1.5, "3", None, MAX_OUTPUT_POINTS + 1])
def test_output_points_are_bounded(store, count) -> None:
    candles = _fill(store, count=40)
    with pytest.raises(ValidationFailure):
        _read(store, candles, 30, "SMA", {}, count=count)


def test_the_largest_allowed_request_reads_a_bounded_span_and_every_menu_maximum_fits() -> None:
    assert MAX_OUTPUT_POINTS == 500
    assert 100_000 >= MAX_HISTORY_CANDLES >= 13_499
    worst = 0
    for spec in INDICATOR_MENU:
        maxima = {parameter.name: parameter.maximum for parameter in spec.parameters}
        if spec.name == "MACD":
            maxima["fastperiod"] = 499
        plan = plan_history(spec.name, maxima, MAX_OUTPUT_POINTS)
        worst = max(worst, plan.span)
        assert plan.span <= MAX_HISTORY_CANDLES, spec.name
    assert worst == 13_499  # ADX at 500: 999 + 12,000 + 500 points


def test_a_request_beyond_the_history_bound_is_refused_before_the_store_is_touched(store, monkeypatch) -> None:
    monkeypatch.setattr(engine, "MAX_HISTORY_CANDLES", 100)
    candles = _fill(store, count=40)
    with pytest.raises(ValidationFailure):
        _read(store, candles, 30, "RSI", {})


def test_requests_outside_the_menu_are_refused(store) -> None:
    candles = _fill(store, count=40)
    for name, params in (("NOT_AN_INDICATOR", {}), ("RSI", {"timeperiod": 1}), ("RSI", {"timeperiod": 14.0}),
                         ("MACD", {"fastperiod": 26, "slowperiod": 12}), ("SMA", {"unknown": 3})):
        with pytest.raises(ValidationFailure):
            _read(store, candles, 30, name, params)
    with pytest.raises(ValidationFailure):
        compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=7, source=_SOURCE,
                          end_close_time=_end(candles, 30), as_of=_end(candles, 30), count=1)
    with pytest.raises(ValidationFailure):
        compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source="somewhere_else",
                          end_close_time=_end(candles, 30), as_of=_end(candles, 30), count=1)
    with pytest.raises(ValidationFailure):
        compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                          end_close_time=_end(candles, 30) + timedelta(seconds=1), as_of=_end(candles, 30), count=1)
    # Both dates are valid, but a span of 20 slots ending there runs before year 1.
    for year_one in (datetime(1, 1, 1, 0, 15, tzinfo=UTC), datetime(1, 1, 1, 4, 30, tzinfo=UTC)):
        for mode in ("point_in_time", "replay"):
            with pytest.raises(ValidationFailure, match="date"):
                compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                                  end_close_time=year_one, as_of=year_one, count=1, mode=mode)  # type: ignore[arg-type]
    for name, params in (("SMA", {"timeperiod": 10**400}), ("BBANDS", {"nbdevup": 10**400}),
                         ("SAR", {"acceleration": 10**309}), ("RSI", {"timeperiod": -(10**400)})):
        with pytest.raises(ValidationFailure):
            _read(store, candles, 30, name, params)
        with pytest.raises(ValidationFailure):
            plan_history(name, params, 1)
    naive = _end(candles, 30).replace(tzinfo=None)
    for end, as_of in ((naive, _end(candles, 30)), (_end(candles, 30), naive), ("2026-01-05T00:15:00Z", None),
                       (_end(candles, 30), 12345)):
        with pytest.raises(ValidationFailure, match="timezone-aware"):
            compute_indicator(store, "SMA", {}, symbol=_SYMBOL, interval_minutes=15, source=_SOURCE,
                              end_close_time=end, as_of=as_of, count=1, mode="replay")  # type: ignore[arg-type]


# --- the record the Trader and the receipts see ------------------------------------------------------------------


def test_the_result_document_is_plain_json_with_the_provenance_a_receipt_needs(store) -> None:
    candles = _fill(store, count=120)
    result = _read(store, candles, 99, "BBANDS", {"timeperiod": 10}, count=2)
    document = result.to_document()
    json.dumps(document, allow_nan=False)  # numbers only: no NaN, no objects
    assert document["indicator"] == "BBANDS" and document["status"] == "ready"
    assert document["parameters"] == {"timeperiod": 10, "nbdevup": 2.0, "nbdevdn": 2.0}
    assert document["values"].keys() == {"upper", "middle", "lower"}
    assert document["close_times_utc"][-1].endswith("Z")
    assert document["as_of_utc"] == utc_iso(result.as_of_utc)
    assert document["library"] == library_versions()
    assert document["spec_version"] == result.spec_version
    assert document["candles"]["count"] == 11 and document["candles"]["run_ids"] == [1]
    assert document["candles"]["revised"] == [] and len(document["candles"]["input_sha256"]) == 64
    assert document["missing_close_times_utc"] == [] and document["missing_count"] == 0


def test_a_not_ready_document_lists_the_missing_slots_and_no_values(store) -> None:
    candles = _fill(store, count=120, skip=(95, 96))
    document = _read(store, candles, 99, "SMA", {"timeperiod": 20}).to_document()
    assert document["status"] == "gapped" and document["values"] == {} and document["close_times_utc"] == []
    assert document["missing_count"] == 2 and len(document["missing_close_times_utc"]) == 2
    json.dumps(document, allow_nan=False)


def test_a_market_with_no_movement_gets_the_librarys_zero_convention_labelled_ready(store) -> None:
    """Where a ratio is 0/0 (nothing moved, no volume) TA-Lib returns 0 and the engine says ready.

    This pins the convention so a library change is noticed, and so nobody reads the zeros as measurements: a ready
    RSI of 0 means 'all losses' only when something moved. The engine does not detect the degenerate case (see
    docs/INDICATOR-MENU.md, rule 8); the Trader reads such oscillators next to the candles it already receives.
    """
    flat = [c.model_copy(update={"open": Decimal("0.5"), "high": Decimal("0.5"), "low": Decimal("0.5"),
                                 "close": Decimal("0.5"), "volume": Decimal("0"), "vwap": None})
            for c in build_candles("range_burst", count=1000)]
    store.save(flat, run_id=_run(store, flat[-1].available_at_utc + timedelta(hours=1)))
    zero_over_zero = {"RSI": ("value",), "MFI": ("value",), "STOCH": ("slow_k", "slow_d"), "WILLR": ("value",),
                      "CCI": ("value",), "ADX": ("value",), "PLUS_DI": ("value",), "MINUS_DI": ("value",)}
    for spec in INDICATOR_MENU:
        result = _read(store, flat, 999, spec.name, {}, count=2)
        assert result.status == "ready", spec.name
        for output, series in result.values.items():
            if output in zero_over_zero.get(spec.name, ()):
                assert series == (0.0, 0.0), (spec.name, output)  # 0/0 read as 0, not as 'all losses' and so on
            elif spec.name == "AROON":
                assert series == (100.0, 100.0), (spec.name, output)
            elif spec.group in ("moving_average", "price_range") or spec.name in ("BBANDS", "SAR"):
                assert series == (0.5, 0.5), (spec.name, output)  # these are simply the price
            else:
                assert series == (0.0, 0.0), (spec.name, output)  # no movement, no range, no volume


def test_a_document_lists_the_first_hundred_missing_slots_and_the_complete_count(store) -> None:
    candles = _fill(store, count=100)
    result = _read(store, candles, 99, "SMA", {"timeperiod": 300})  # 200 of the 300 slots are before the series
    document = result.to_document()
    assert result.status == "warming_up" and len(result.missing_close_times_utc) == 200
    assert engine.DOCUMENT_MISSING_LIMIT == 100
    assert document["missing_count"] == 200 and len(document["missing_close_times_utc"]) == 100
    assert document["missing_close_times_utc"] == [utc_iso(at) for at in result.missing_close_times_utc[:100]]
    assert document["available_candles"] == 100 and document["required_candles"] == 300
    json.dumps(document, allow_nan=False)


def test_the_spec_version_changes_when_the_menu_entry_changes() -> None:
    first = engine.spec_version(get_indicator("RSI"))
    assert first == engine.spec_version(get_indicator("RSI"))
    assert first != engine.spec_version(get_indicator("SMA"))
    changed = get_indicator("RSI").model_copy(update={"description": get_indicator("RSI").description[:-1] + " today."})
    assert first != engine.spec_version(changed)


# --- the boundary of the engine ----------------------------------------------------------------------------------


def test_the_engine_reads_candles_only_through_the_store_window_and_never_imports_a_library() -> None:
    tree = ast.parse(_ENGINE_SOURCE.read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                 and node.module and node.level == 0}
    assert not imported & {"talib", "numpy", "pandas", "sqlite3", "statistics", "scipy"}
    from_names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "MarketDataDatabase" not in from_names
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "window" in attributes
    assert not attributes & {"database", "query", "execute", "executemany", "coverage", "revisions", "series"}
    # No arithmetic or aggregation on a candle price or volume field (this guard sees a hand-written max or min).
    from tests.indicator_guard import arithmetic_on_prices

    assert arithmetic_on_prices(_ENGINE_SOURCE) == []
    # No arithmetic on prices or indicator values: the only multiplication is the menu's settling multiplier.
    operators = [type(node.op).__name__ for node in ast.walk(tree) if isinstance(node, ast.BinOp)]
    assert "Div" not in operators and "Pow" not in operators and "MatMult" not in operators
