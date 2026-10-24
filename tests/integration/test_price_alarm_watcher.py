"""The price watcher (T33) on synthetic quotes and a synthetic candle store, with a frozen clock. No network.

Covers the acceptance: no double fire across restarts, no fire from a still-forming candle, batched delivery,
watching costs no model tokens, feed loss is reported (never a quiet market), and no limit beyond one-shot.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.alarm_support import (
    NOW,
    candle,
    candle_request,
    indicator_request,
    quote,
    quote_request,
    save,
)
from tests.indicator_series import build_candles

from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.alarms import book as book_module
from trade_graph.application.alarms import queue as queue_module
from trade_graph.application.alarms import watcher as watcher_module
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.alarms.queue import AlarmQueue
from trade_graph.application.alarms.watcher import PriceWatcher, WatchPolicy
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.engine import CandleSelectionError, compare_to_level, compute_indicator
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 16, hour, minute, second, tzinfo=UTC)


def _to(clock: FrozenClock, when: datetime) -> None:
    assert when >= clock.now(), "a test clock only moves forward"
    clock.advance((when - clock.now()).total_seconds())


class Rig:
    """One installation: state database, candle store, book, queue and watcher, reopenable to simulate a restart."""

    def __init__(self, root: Path, clock: FrozenClock, policy: WatchPolicy | None = None) -> None:
        self.root, self.clock, self.policy = root, clock, policy
        self.open()

    def open(self) -> None:
        self.state = Database(self.root / "state.sqlite")
        self.market = MarketDataDatabase(self.root / "market" / "market_data.sqlite")
        self.store = CandleStore(self.market)
        self.book = AlarmBook(self.state, self.clock)
        self.queue = AlarmQueue(self.state, self.clock)
        self.watcher = PriceWatcher(self.book, self.store, self.clock, policy=self.policy or WatchPolicy())

    def restart(self) -> None:
        self.close()
        self.open()

    def close(self) -> None:
        self.state.close()
        self.market.close()

    def fires(self) -> int:
        return self.state.execute("SELECT COUNT(*) FROM price_alarm_events WHERE action = 'fired'").fetchone()[0]


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def rig(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    yield rig
    rig.close()


# --- live quotes ----------------------------------------------------------------------------------------------


def test_a_quote_alarm_fires_once_on_the_first_update_past_its_level(rig, clock) -> None:
    alarm = rig.book.create(quote_request("65000", "above"))
    assert rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="64980", ask="64990")) == []
    clock.advance(10)
    fired = rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65005", ask="65015"))
    assert [item.alarm_id for item in fired] == [alarm.alarm_id]
    observed = fired[0].document["observed"]
    assert observed["basis"] == "quote" and observed["price"] == "mid" and observed["value"] == "65010"
    assert observed["available_at_utc"] == utc_iso(clock.now())
    clock.advance(10)
    assert rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65100", ask="65110")) == []
    assert rig.fires() == 1 and rig.book.get(alarm.alarm_id).status == "spent"


def test_a_crossing_needs_a_reading_on_each_side_and_survives_a_restart(rig, clock) -> None:
    alarm = rig.book.create(quote_request("65000", "crosses_above"))
    already_above = quote("BTC/USD", clock.now(), bid="65100", ask="65100")
    assert rig.watcher.observe_quote(already_above) == []  # no reading below yet: no crossing
    clock.advance(10)
    assert rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="64890", ask="64910")) == []
    below_at = clock.now()
    rig.restart()
    clock.advance(10)
    fired = rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65040", ask="65060"))
    assert [item.alarm_id for item in fired] == [alarm.alarm_id]
    observed = fired[0].document["observed"]
    assert (observed["value"], observed["previous_value"], observed["previous_at"]) == (
        "65050", "64900", utc_iso(below_at))
    assert observed["seconds_since_previous_quote"] is None  # this watcher had seen no quote since its restart


def test_each_quote_price_can_be_watched_and_a_missing_one_is_not_guessed(rig, clock) -> None:
    bid = rig.book.create(quote_request("64000", "below", price="bid"))
    ask = rig.book.create(quote_request("66000", "above", price="ask"))
    last = rig.book.create(quote_request("65500", "above", price="last"))
    assert rig.watcher.observe_quote(quote("BTC/USD", clock.now(), last="65600")) == [
        item for item in rig.queue.pending("trader") if item.alarm_id == last.alarm_id]
    clock.advance(1)
    assert rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="63990")) != []  # no ask: ask alarm waits
    assert rig.book.get(bid.alarm_id).status == "spent" and rig.book.get(ask.alarm_id).status == "armed"


def test_quotes_from_the_future_are_refused_and_stale_ones_do_not_move_a_baseline(rig, clock) -> None:
    rig.book.create(quote_request("65000", "crosses_below"))
    with pytest.raises(ValidationFailure, match="future"):
        rig.watcher.observe_quote(quote("BTC/USD", clock.now() + timedelta(seconds=1), bid="1", ask="1"))
    clock.advance(20)
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65100", ask="65100"))  # held: above, at +20 s
    alarm = rig.book.armed()[0]
    held = rig.book.baseline(alarm.alarm_id, 1)
    older = quote("BTC/USD", clock.now() - timedelta(seconds=10), bid="64000", ask="64000")  # after arming
    assert rig.watcher.observe_quote(older) == []
    assert rig.fires() == 0  # a reading older than the one the alarm already holds is not a crossing
    assert rig.book.baseline(alarm.alarm_id, 1) == held
    clock.advance(1)
    assert len(rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="64000", ask="64000"))) == 1


# --- completed candles ----------------------------------------------------------------------------------------


def test_no_fire_from_a_still_forming_candle(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    alarm = rig.book.create(candle_request("105", "above"))
    _to(clock, _at(12, 14, 59))
    rig.watcher.observe_quote(quote("SYN/USD", clock.now(), bid="110", ask="110"))  # the forming candle is high
    assert rig.watcher.tick().fired == ()
    _to(clock, _at(12, 15, 10))
    assert rig.watcher.tick().fired == ()  # closed, but not received yet
    save(rig.store, [candle(_at(12, 15), "110", received_after_seconds=30)])
    assert rig.watcher.tick().fired == ()  # received at 12:15:30, not visible at 12:15:10
    assert rig.book.get(alarm.alarm_id).evaluated_through is None
    _to(clock, _at(12, 15, 40))
    fired = rig.watcher.tick().fired
    assert [item.alarm_id for item in fired] == [alarm.alarm_id]
    observed = fired[0].document["observed"]
    assert observed["close_time_utc"] == utc_iso(_at(12, 15)) and observed["value"] == "110"
    assert observed["candle"]["available_at_utc"] == utc_iso(_at(12, 15, 30))


def test_a_candle_alarm_never_fires_twice_across_restarts_and_rearms_from_the_next_candle(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    alarm = rig.book.create(candle_request("105", "above"))
    save(rig.store, [candle(_at(12, 15), "110")])
    _to(clock, _at(12, 16))
    assert len(rig.watcher.tick().fired) == 1
    rig.restart()
    clock.advance(30)
    assert rig.watcher.tick().fired == ()
    assert rig.fires() == 1 and len(rig.queue.pending("trader")) == 1
    _to(clock, _at(12, 17))
    rig.book.rearm(alarm.alarm_id, department="trader")
    assert rig.watcher.tick().fired == ()  # the 12:15 candle belongs to the first arming
    save(rig.store, [candle(_at(12, 30), "111")])
    _to(clock, _at(12, 31))
    fired = rig.watcher.tick().fired
    assert [(item.generation, item.document["observed"]["close_time_utc"]) for item in fired] == [
        (2, utc_iso(_at(12, 30)))]


def test_candles_missed_while_the_watcher_was_down_are_evaluated_in_order(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    alarm = rig.book.create(candle_request("105", "crosses_above"))
    save(rig.store, [candle(_at(12, 15), "100"), candle(_at(12, 30), "110"), candle(_at(12, 45), "100")])
    _to(clock, _at(13, 1))
    fired = rig.watcher.tick().fired
    assert [item.alarm_id for item in fired] == [alarm.alarm_id]
    observed = fired[0].document["observed"]
    assert (observed["close_time_utc"], observed["value"], observed["previous_value"]) == (
        utc_iso(_at(12, 30)), "110", "100")


def test_a_missing_candle_is_waited_for_then_reported_and_stepped_over(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    plain = rig.book.create(candle_request("105", "above"))
    crossing = rig.book.create(candle_request("105", "crosses_above"))
    save(rig.store, [candle(_at(12, 15), "100")])
    _to(clock, _at(12, 40))
    assert rig.watcher.tick().fired == ()
    assert rig.book.get(plain.alarm_id).evaluated_through == utc_iso(_at(12, 15))  # waits at 12:30
    save(rig.store, [candle(_at(12, 45), "110")])
    _to(clock, _at(12, 46))
    fired = rig.watcher.tick().fired
    assert [item.alarm_id for item in fired] == [plain.alarm_id]
    assert "12:30" in rig.book.get(plain.alarm_id).last_reason
    # A crossing compares two consecutive candles; with 12:30 missing, 12:45 alone shows no crossing.
    record = rig.book.get(crossing.alarm_id)
    assert record.status == "armed" and record.evaluated_through == utc_iso(_at(12, 45))
    assert "previous candle" in record.last_reason


def test_high_and_low_are_watched_on_completed_candles(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    wick = rig.book.create(candle_request("108", "above", price="high"))
    dip = rig.book.create(candle_request("95", "below", price="low"))
    save(rig.store, [candle(_at(12, 15), "101", high="109", low="99")])
    _to(clock, _at(12, 16))
    assert [item.alarm_id for item in rig.watcher.tick().fired] == [wick.alarm_id]
    assert rig.book.get(dip.alarm_id).status == "armed"


def _indicator_rig(tmp_path: Path, count: int, interval: int = 15) -> tuple[Rig, FrozenClock, list]:
    candles = build_candles("trend_pullback", interval_minutes=interval, count=count)
    clock = FrozenClock(candles[0].close_time_utc)
    rig = Rig(tmp_path, clock)
    save(rig.store, candles)
    return rig, clock, candles


def _first_engine_fire(rig: Rig, candles: list, start: int, stop: int, name: str, parameters: dict, level: Decimal,
                       relation: str) -> int | None:
    """The oracle: ask the engine directly, candle by candle, as of each candle's receipt."""
    for index in range(start, stop):
        result = compute_indicator(rig.store, name, parameters, symbol="SYN/USD", interval_minutes=15,
                                   source="synthetic", end_close_time=candles[index].close_time_utc,
                                   as_of=candles[stop - 1].available_at_utc + timedelta(seconds=1),
                                   count=2 if relation.startswith("crosses") else 1)
        if compare_to_level(result, level, relation):
            return index
    return None


@pytest.mark.parametrize(("name", "parameters", "relation", "count"), [
    ("SMA", {"timeperiod": 20}, "crosses_above", 500),
    ("RSI", {"timeperiod": 14}, "crosses_below", 900),
])
def test_an_indicator_alarm_fires_at_the_first_candle_where_the_engine_comparison_holds(
        tmp_path, name, parameters, relation, count) -> None:
    rig, clock, candles = _indicator_rig(tmp_path, count)
    try:
        start, stop = count - 60, count
        armed_at = candles[start - 1].available_at_utc + timedelta(seconds=1)
        middle = compute_indicator(rig.store, name, parameters, symbol="SYN/USD", interval_minutes=15,
                                   source="synthetic", end_close_time=candles[stop - 1].close_time_utc,
                                   as_of=candles[stop - 1].available_at_utc, count=60)
        values = middle.values["value"]
        level = Decimal(str(round(sum(values) / len(values), 4)))  # a level the series moves through
        expected = _first_engine_fire(rig, candles, start, stop, name, parameters, level, relation)
        assert expected is not None, "the test series must cross its own average"
        _to(clock, armed_at)
        alarm = rig.book.create(indicator_request(name, str(level), relation, parameters=parameters))
        _to(clock, candles[stop - 1].available_at_utc + timedelta(seconds=1))
        fired = rig.watcher.tick().fired
        assert [item.alarm_id for item in fired] == [alarm.alarm_id]
        observed = fired[0].document["observed"]
        assert observed["close_time_utc"] == utc_iso(candles[expected].close_time_utc)
        assert observed["indicator"]["name"] == name and observed["indicator"]["status"] == "ready"
        assert len(observed["indicator"]["input_sha256"]) == 64
    finally:
        rig.close()


def test_an_indicator_that_is_not_ready_never_fires_and_says_why(tmp_path) -> None:
    candles = build_candles("trend_pullback", interval_minutes=5, count=100)
    clock = FrozenClock(candles[49].available_at_utc + timedelta(seconds=1))
    rig = Rig(tmp_path, clock)
    try:
        save(rig.store, candles)
        alarm = rig.book.create(indicator_request("RSI", "1", "above", interval=5))  # RSI needs 794 candles
        _to(clock, candles[-1].available_at_utc + timedelta(seconds=1))
        assert rig.watcher.tick().fired == ()
        record = rig.book.get(alarm.alarm_id)
        assert record.status == "armed" and "warming up" in record.last_reason
        assert record.evaluated_through == utc_iso(candles[-1].close_time_utc)
    finally:
        rig.close()


# --- batching, feeds, cost and limits -------------------------------------------------------------------------


def test_alarms_that_fire_while_the_trader_is_awake_arrive_together_at_its_next_wake(rig, clock) -> None:
    first = rig.book.create(quote_request("65000", "above"))
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65001", ask="65001"))
    wake = rig.queue.claim("trader", "wake-1")
    assert [item.alarm_id for item in wake.items] == [first.alarm_id]
    second = rig.book.create(quote_request("65500", "above"))
    third = rig.book.create(quote_request("64000", "below"))
    clock.advance(5)
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65600", ask="65600"))
    clock.advance(5)
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="63900", ask="63900"))
    rig.queue.acknowledge("wake-1")
    assert [item.alarm_id for item in rig.queue.claim("trader", "wake-2").items] == [second.alarm_id, third.alarm_id]


def test_a_lost_quote_feed_is_reported_never_treated_as_a_quiet_market(rig, clock) -> None:
    alarm = rig.book.create(quote_request("70000", "above"))
    clock.advance(60)
    assert rig.watcher.tick().feeds == {"quote:BTC/USD": "ok"}
    clock.advance(90)
    report = rig.watcher.tick()
    assert report.feeds == {"quote:BTC/USD": "lost"} and report.blind_alarms == (alarm.alarm_id,)
    notices = rig.queue.pending("trader")
    assert [item.kind for item in notices] == ["feed_lost"]
    assert notices[0].document["armed_alarm_ids"] == [alarm.alarm_id]
    clock.advance(30)
    assert rig.watcher.tick().feed_notices == ()  # still lost: told once
    rig.restart()
    clock.advance(30)
    assert rig.watcher.tick().feed_notices == () and len(rig.queue.pending("trader")) == 1  # not repeated
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), last="65000"))
    assert [item.kind for item in rig.queue.pending("trader")] == ["feed_lost", "feed_restored"]
    assert rig.watcher.tick().feeds == {"quote:BTC/USD": "ok"}


def test_a_candle_feed_that_stops_is_reported_and_its_return_too(rig, clock) -> None:
    save(rig.store, [candle(_at(12, 0), "100")])
    rig.book.create(candle_request("105", "above"))
    _to(clock, _at(12, 20))
    assert rig.watcher.tick().feeds == {"candle:SYN/USD:15:synthetic": "ok"}  # 12:15 is late but within grace
    _to(clock, _at(12, 26))
    report = rig.watcher.tick()
    assert report.feeds == {"candle:SYN/USD:15:synthetic": "lost"}
    assert [item.kind for item in report.feed_notices] == ["feed_lost"]
    save(rig.store, [candle(_at(12, 15), "100")])
    _to(clock, _at(12, 27))
    assert [item.kind for item in rig.watcher.tick().feed_notices] == ["feed_restored"]


def test_watching_costs_no_model_tokens(rig, clock) -> None:
    forbidden = ("trade_graph.adapters.models", "trade_graph.adapters.research", "trade_graph.application.budget",
                 "trade_graph.application.gateway", "trade_graph.application.model_invocations",
                 "trade_graph.application.runtime_models", "trade_graph.kernel", "trade_graph.orchestration",
                 "openai", "anthropic", "httpx", "langgraph")
    for module in (book_module, queue_module, watcher_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not [name for name in names if name.startswith(forbidden)], module.__name__
    save(rig.store, [candle(_at(12, 0), "100")])
    rig.book.create(candle_request("105", "above"))
    rig.book.create(quote_request("65000", "above"))
    save(rig.store, [candle(_at(12, 15), "110")])
    _to(clock, _at(12, 16))
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65100", ask="65100"))
    assert len(rig.watcher.tick().fired) == 1 and rig.fires() == 2
    for table in ("budget_reservations", "usage_receipts"):
        assert rig.state.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_no_limit_on_alarm_count_or_firing_frequency(rig, clock) -> None:
    """D116: 2,000 alarms fire on one update, and one alarm re-armed 100 times fires 100 times. Only one-shot."""
    alarms = [rig.book.create(quote_request(str(60000 + index), "above")) for index in range(2000)]
    fired = rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="70000", ask="70000"))
    assert len(fired) == 2000
    assert len(rig.queue.claim("trader", "wake-many").items) == 2000
    rig.queue.acknowledge("wake-many")
    alarm = alarms[0]
    for generation in range(2, 102):
        rig.book.rearm(alarm.alarm_id, department="trader", after_generation=generation - 1)
        clock.advance(1)
        assert len(rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="70000", ask="70000"))) == 1
    assert rig.book.get(alarm.alarm_id).generation == 101
    assert len(rig.queue.pending("trader")) == 100


def test_one_alarm_that_cannot_be_evaluated_does_not_blind_the_others(rig, clock) -> None:
    class BrokenForOneSymbol(CandleStore):
        def window(self, symbol, *args, **kwargs):
            if symbol == "BAD/USD":
                raise CandleSelectionError("the store returned a candle of another slot")
            return super().window(symbol, *args, **kwargs)

    rig.watcher = PriceWatcher(rig.book, BrokenForOneSymbol(rig.market), clock)
    save(rig.store, [candle(_at(12, 0), "100")])
    broken = rig.book.create(candle_request("105", "above", symbol="BAD/USD"))
    healthy = rig.book.create(candle_request("105", "above"))
    save(rig.store, [candle(_at(12, 15), "110")])
    _to(clock, _at(12, 16))
    report = rig.watcher.tick()
    assert [item.alarm_id for item in report.fired] == [healthy.alarm_id]
    assert [alarm_id for alarm_id, _ in report.errors] == [broken.alarm_id]
    record = rig.book.get(broken.alarm_id)
    assert record.status == "armed" and record.evaluated_through is None  # the cursor did not move
    assert "candle_selection_invalid" in record.last_reason
    assert report.feeds["candle:BAD/USD:15:synthetic"] == "lost"  # a series that cannot be read is not quiet


def test_a_hole_longer_than_one_catch_up_step_is_stepped_over_once_candles_return(rig, clock, monkeypatch) -> None:
    monkeypatch.setattr(watcher_module, "CATCH_UP_SLOTS", 3)
    save(rig.store, [candle(_at(12, 0), "100")])
    alarm = rig.book.create(candle_request("105", "above"))
    _to(clock, _at(13, 40))
    assert rig.watcher.tick().fired == ()
    assert rig.book.get(alarm.alarm_id).evaluated_through is None  # nothing later exists yet: wait
    save(rig.store, [candle(_at(13, 45), "110")])  # 12:15 to 13:30 never arrive (two empty catch-up steps)
    _to(clock, _at(13, 46))
    fired = rig.watcher.tick().fired
    assert [item.document["observed"]["close_time_utc"] for item in fired] == [utc_iso(_at(13, 45))]
    assert "no candle" in rig.book.get(alarm.alarm_id).last_reason


def test_a_department_that_arms_on_a_feed_already_lost_is_told_too(rig, clock) -> None:
    rig.book.create(quote_request("70000", "above", department="trader"))
    clock.advance(150)
    rig.watcher.tick()
    assert [item.kind for item in rig.queue.pending("trader")] == ["feed_lost"]
    late = rig.book.create(quote_request("71000", "above", department="research"))
    clock.advance(5)
    notices = rig.watcher.tick().feed_notices
    assert [(item.department, item.kind) for item in notices] == [("research", "feed_lost")]
    assert notices[0].document["armed_alarm_ids"] == [late.alarm_id]
    assert notices[0].document["feed_event_id"] == rig.queue.pending("trader")[0].document["feed_event_id"]
    assert len(rig.queue.pending("trader")) == 1  # the Trader is not told twice
    clock.advance(5)
    assert rig.watcher.tick().feed_notices == ()
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65000", ask="65000"))
    assert rig.queue.waiting() == {"research": 2, "trader": 2}


def test_a_backlog_quote_older_than_the_stale_limit_does_not_restore_a_lost_feed(rig, clock) -> None:
    rig.book.create(quote_request("70000", "above"))
    clock.advance(300)
    rig.watcher.tick()
    rig.watcher.observe_quote(quote("BTC/USD", clock.now() - timedelta(seconds=200), bid="65000", ask="65000"))
    assert [item.kind for item in rig.queue.pending("trader")] == ["feed_lost"]
    assert rig.watcher.tick().feeds == {"quote:BTC/USD": "lost"}
    rig.watcher.observe_quote(quote("BTC/USD", clock.now(), bid="65000", ask="65000"))
    assert [item.kind for item in rig.queue.pending("trader")] == ["feed_lost", "feed_restored"]


def test_an_alarm_whose_stored_condition_the_menu_no_longer_accepts_is_reported_not_fatal(rig, clock) -> None:
    quote_alarm = rig.book.create(quote_request("70000", "above"))
    retired = "retired-indicator-alarm"
    condition = ('{"indicator":"RETIRED","interval_minutes":15,"kind":"indicator","level":"5","output":"value",'
                 '"parameters":{"timeperiod":14},"relation":"above","source":"synthetic","symbol":"SYN/USD"}')
    with rig.state.immediate() as connection:  # a row written under an older menu that has since changed
        connection.execute(
            """INSERT INTO price_alarms (alarm_id, department, basis, kind, symbol, interval_minutes, source,
               condition_json, contract_version, created_at)
               VALUES (?, 'trader', 'candle', 'indicator', 'SYN/USD', 15, 'synthetic', ?, 'price-alarm-v1', ?)""",
            (retired, condition, utc_iso(clock.now())))
        connection.execute("""INSERT INTO price_alarm_events (event_id, alarm_id, generation, action, at,
                              document_json) VALUES ('retired-armed', ?, 1, 'armed', ?, '{}')""",
                           (retired, utc_iso(clock.now())))
        connection.execute("""INSERT INTO price_alarm_watch (alarm_id, status, generation, armed_at, updated_at)
                              VALUES (?, 'armed', 1, ?, ?)""", (retired, utc_iso(clock.now()), utc_iso(clock.now())))
    assert {record.alarm_id for record in rig.book.armed()} == {quote_alarm.alarm_id, retired}
    save(rig.store, [candle(_at(12, 15), "100")])
    _to(clock, _at(12, 16))
    report = rig.watcher.tick()
    assert [alarm_id for alarm_id, _ in report.errors] == [retired]
    assert "not on the indicator menu" in rig.book.get(retired).last_reason
    assert report.feeds["quote:BTC/USD"] == "lost"  # feed health still runs for everything else
