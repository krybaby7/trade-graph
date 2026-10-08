"""Recurring public history uses real receipts and UTC completed-hour boundaries."""

import asyncio
import json
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration.test_execution import _stack
from tests.integration.test_paper_service import Feed

from trade_graph.application.price_history import HistoryStore
from trade_graph.application.subscription_operation import _HistoryRefreshingFeed, _NormalSubscriptionService
from trade_graph.domain.clock import utc_iso


class ClockedHistoryTransport:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.failed_symbols = set()
        self.lagging_symbols = set()
        self.entered = self.finish = None
        self.delay_seconds = 0

    def get_text(self, url):
        symbol = "BTC/USD" if "pair=XBTUSD" in url else "ETH/USD"
        self.calls.append((symbol, self.clock.now()))
        if self.entered is not None:
            self.entered.set()
            assert self.finish.wait(5)
        self.clock.advance(self.delay_seconds)
        if symbol in self.failed_symbols:
            raise OSError("synthetic private transport detail")
        end = self.clock.now().replace(minute=0, second=0, microsecond=0)
        offsets = (3, 2, 1) if symbol in self.lagging_symbols else (2, 1, 0)
        rows = [[int((end - timedelta(hours=offset)).timestamp()),
                 "10", "11", "9", "10", "10", "1", 1] for offset in offsets]
        key = "XXBTZUSD" if symbol == "BTC/USD" else "XETHZUSD"
        return json.dumps({"error": [], "result": {key: rows, "last": int(end.timestamp())}})


def history_flow(tmp_path, *, interval_seconds=3600):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    clock.advance(7 * 60)
    feed = Feed()
    feed.symbols = ["BTC/USD", "ETH/USD"]
    feed.transport = ClockedHistoryTransport(clock)
    runtime = SimpleNamespace(database=ledger.database, clock=clock, ledger=ledger, portfolio_id=portfolio)
    history = _HistoryRefreshingFeed(feed, runtime, hours=2, interval_seconds=interval_seconds)
    return clock, ledger, execution, portfolio, history


@pytest.mark.parametrize("interval_seconds", [3600, 7200])
def test_history_refreshes_at_next_utc_hour_without_startup_minute_drift(tmp_path, interval_seconds):
    clock, ledger, _execution, _portfolio, history = history_flow(tmp_path, interval_seconds=interval_seconds)
    history.poll()
    original_as_of = clock.now()
    store = HistoryStore(ledger.database)
    original = store.snapshot("BTC/USD", original_as_of, ["hourly_return"])
    clock.advance(53 * 60)
    history.poll()
    assert [symbol for symbol, _at in history.transport.calls] == ["BTC/USD", "ETH/USD"] * 2
    current = store.snapshot("BTC/USD", clock.now(), ["hourly_return"])
    assert current["latest_close_time_utc"] == utc_iso(clock.now())
    assert current["stale"] is False
    assert store.snapshot("BTC/USD", original_as_of, ["hourly_return"]) == original


def test_failed_symbol_retries_after_thirty_seconds_without_refetching_success(tmp_path):
    clock, _ledger, _execution, _portfolio, history = history_flow(tmp_path)
    history.transport.failed_symbols = {"ETH/USD"}
    history.poll()
    assert history.last_history["status"] == "partial"
    clock.advance(29)
    history.poll()
    assert len(history.transport.calls) == 2
    history.transport.failed_symbols.clear()
    clock.advance(1)
    history.poll()
    assert [symbol for symbol, _at in history.transport.calls] == ["BTC/USD", "ETH/USD", "ETH/USD"]
    assert history.last_history["status"] == "ok"
    assert set(history.last_history["symbols"]) == {"BTC/USD", "ETH/USD"}


def test_slow_hour_boundary_refresh_finishes_before_next_role_inputs_while_management_continues(tmp_path):
    clock, ledger, execution, portfolio, history = history_flow(tmp_path)
    seen = []
    service = _NormalSubscriptionService(ledger.database, execution, clock=clock, public_feed=history,
        handlers={"research": lambda _task: seen.append(HistoryStore(ledger.database).snapshot(
            "BTC/USD", clock.now(), ["hourly_return"])) or {}},
        schedule_intervals={"research": 3600}, tick_interval_seconds=0.01)

    async def scenario():
        await service.tick(wait_roles=True)
        assert len(seen) == 1
        clock.advance(60 * 60)
        history.transport.delay_seconds = 2
        history.transport.entered, history.transport.finish = threading.Event(), threading.Event()
        management = []
        original_management = service._management
        def recorded_management():
            management.append(clock.now())
            return original_management()
        service._management = recorded_management
        tick = asyncio.create_task(service.tick(wait_roles=True))
        try:
            assert await asyncio.to_thread(history.transport.entered.wait, 5)
            await asyncio.sleep(0.05)
            assert len(seen) == 1
            assert len(management) >= 2
        finally:
            history.transport.finish.set()
            await tick
            await service.stop()
        assert len(seen) == 2
        assert seen[-1]["stale"] is False
        assert seen[-1]["available_at_utc"] == utc_iso(clock.now() - timedelta(seconds=2))
        assert seen[-1]["available_at_utc"] != seen[-1]["event_time_utc"]

    asyncio.run(scenario())


def test_pre_hour_quote_poll_cannot_satisfy_next_hour_history_barrier(tmp_path):
    clock, ledger, execution, _portfolio, history = history_flow(tmp_path)
    seen = []
    service = _NormalSubscriptionService(ledger.database, execution, clock=clock, public_feed=history,
        handlers={"research": lambda _task: seen.append(HistoryStore(ledger.database).snapshot(
            "BTC/USD", clock.now(), ["hourly_return"])) or {}},
        schedule_intervals={"research": 53 * 60}, tick_interval_seconds=0.01)

    async def scenario():
        await service.tick(wait_roles=True)
        entered, finish = threading.Event(), threading.Event()
        original_poll = history.feed.poll
        def held_quote_poll():
            entered.set()
            assert finish.wait(5)
            return original_poll()
        history.feed.poll = held_quote_poll
        clock.advance(52 * 60)
        await service.tick(wait_roles=True)
        assert await asyncio.to_thread(entered.wait, 5)
        clock.advance(60)
        tick = asyncio.create_task(service.tick(wait_roles=True))
        try:
            await asyncio.sleep(0.03)
        finally:
            finish.set()
            await tick
            await service.stop()
        assert len(seen) == 2
        assert seen[-1]["stale"] is False
        assert seen[-1]["latest_close_time_utc"] == utc_iso(clock.now())
        assert len(history.transport.calls) == 4

    asyncio.run(scenario())


def test_valid_response_missing_newly_completed_tail_retries_promptly(tmp_path):
    clock, ledger, _execution, _portfolio, history = history_flow(tmp_path)
    history.transport.lagging_symbols = {"ETH/USD"}
    history.poll()
    assert history.last_history["symbols"]["ETH/USD"]["status"] == "ok"
    assert history.last_history["symbols"]["ETH/USD"]["missing_hours"] == 1
    assert HistoryStore(ledger.database).snapshot("ETH/USD", clock.now(), ["hourly_return"])["stale"]
    history.transport.lagging_symbols.clear()
    clock.advance(30)
    history.poll()
    assert [symbol for symbol, _at in history.transport.calls] == ["BTC/USD", "ETH/USD", "ETH/USD"]
    assert not HistoryStore(ledger.database).snapshot("ETH/USD", clock.now(), ["hourly_return"])["stale"]
