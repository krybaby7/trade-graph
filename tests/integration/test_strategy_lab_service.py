"""The strategy lab in the paper service, in its own thread like the price collector (W11-B, C11; D290, D292).

The service never waits for the lab: while a long queue runs (here a replay held open on purpose), a resting entry
fills, its safety exit goes out and fills, and management, reconciliation and the money-limit step go on. The queue
survives a restart, and a failing lab step is reported as ``strategy_lab:<error>`` while trading continues. Synthetic
account, candles (``synthetic_fixture``) and quotes; scripted departments; no network, model, key or real order.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.day_plan_support import day_plan_account, entry, install, plan
from tests.strategy_lab_support import candle_file, confirm, register, study, submit, tune, worker_for
from tests.wake_support import scripted

from trade_graph.application.events import EngineOptions
from trade_graph.application.paper_service import PaperService
from trade_graph.application.practice_range.replay import replay
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.contracts.models import Observation

D = Decimal


class Feed:
    """Scripted public quotes: each poll returns the next batch."""

    def __init__(self, account) -> None:
        self.account, self.batches = account, []

    def quote(self, bid: str, ask: str, *, volume: str | None = None) -> None:
        now = self.account.clock.now()
        self.batches.append([Observation(
            observation_id=f"lab-feed-{len(self.batches)}-{now.timestamp()}", venue="paper", symbol="SOL/USD",
            event_time_utc=now, available_at_utc=now, bid=bid, ask=ask, bid_size="1000", ask_size="1000",
            volume=volume, kind="quote", source="synthetic-lab-service")])

    def poll(self):
        return self.batches.pop(0) if self.batches else []

    def close(self) -> None:
        pass


def _service(account, worker, *, feed=None) -> PaperService:
    return PaperService(account.database, account.execution, clock=account.clock, portfolio_ids=[account.portfolio],
                        handlers={"trader": scripted()}, public_feed=feed,
                        event_engine=EngineOptions(daily_cycle=False), lab_worker=worker)


def _long_queue() -> list:
    requests = [register(), study()]
    for day in range(2, 8):
        requests.append(tune(window=(datetime(2026, 9, 1, tzinfo=UTC).date(),
                                     datetime(2026, 9, day, tzinfo=UTC).date()), profiles=("all",)))
    return [*requests, confirm(profiles=("all",))]


@pytest.fixture
def lab(tmp_path):
    account = day_plan_account(tmp_path)
    market, candles = candle_file(tmp_path / "market_data.sqlite")
    yield account, candles
    market.close()


def test_a_safety_exit_fills_while_a_long_queue_is_running(lab) -> None:
    account, candles = lab
    install(account, plan(entries=[entry("5", "99.5", "97", "104")]))
    submit(account, _long_queue())
    held_open, entered = threading.Event(), threading.Event()

    def slow(store, request):  # the first replay waits until the test lets it go: the lab is busy throughout
        entered.set()
        assert held_open.wait(30), "the test never released the replay"
        return replay(store, request)

    worker = worker_for(account, candles, replay_fn=slow)
    feed = Feed(account)
    service = _service(account, worker, feed=feed)
    queue = LabQueue(account.database, account.clock)

    async def scenario():
        await service.tick(wait_feed=True, wait_roles=True)  # sends the resting entry; the lab registers
        for _ in range(3):
            await service.tick(wait_roles=True)
            if entered.is_set():
                break
        assert entered.wait(10) and queue.counts() == {"queued": 6, "running": 1}  # the first tune is held open
        account.clock.advance(1)
        feed.quote("99.4", "99.5", volume="100")  # the entry fills; its safety exit goes out at once
        await service.tick(wait_feed=True, wait_roles=True)
        assert account.held("SOL") == D("5")
        account.clock.advance(1)
        feed.quote("96.9", "97.0", volume="100")  # the bid reaches the stop: the safety exit fills
        await service.tick(wait_feed=True, wait_roles=True)
        assert account.held("SOL") == D("0")
        assert queue.counts() == {"queued": 6, "running": 1}  # the lab is still on its first replay
        held_open.set()
        for _ in range(1000):  # a guard for the test; the lab has no deadline
            if queue.counts() == {"queued": 0, "running": 0}:
                break
            await service.tick()
            await asyncio.sleep(0.05)
        await service.stop()

    asyncio.run(scenario())
    assert queue.counts() == {"queued": 0, "running": 0}
    assert {record.status for record in queue.requests(batch_id="batch-1")} == {"done"}
    sides = [row[0] for row in account.database.execute(
        "SELECT json_extract(payload_json, '$.side') FROM order_intents ORDER BY created_at")]
    assert sides == ["buy", "sell"]  # the day plan's entry and its safety exit; the lab sent nothing


def test_the_queue_survives_a_restart_of_the_service(lab) -> None:
    account, candles = lab
    submit(account, _long_queue())

    async def first_run():
        service = _service(account, worker_for(account, candles))
        await service.tick(wait_roles=True)
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(first_run())
    queue = LabQueue(account.database, account.clock)
    assert 0 < queue.counts()["queued"] < 9  # stopped part-way through

    async def second_run():
        service = _service(account, worker_for(account, candles))
        for _ in range(1000):  # a guard for the test; the lab has no deadline
            if queue.counts() == {"queued": 0, "running": 0}:
                break
            await service.tick()
            await asyncio.sleep(0.02)
        await service.stop()

    asyncio.run(second_run())
    assert {record.status for record in queue.requests(batch_id="batch-1")} == {"done"}
    runs = PracticeRangeStore(account.database, account.clock).runs(strategy_ref="cross-hundred@1")
    assert len(runs) == 7 * 3  # six tunes and one confirmation, under each published profile, each recorded once


def test_a_failing_lab_step_is_reported_and_trading_goes_on(lab) -> None:
    account, _candles = lab

    class Broken:
        def step(self):
            raise RuntimeError("synthetic")

        def request_stop(self):
            pass

        def resume(self):
            pass

    service = _service(account, Broken())

    async def scenario():
        await service.tick(wait_roles=True)
        await asyncio.sleep(0.05)
        result = await service.tick(wait_roles=True)
        await service.stop()
        return result

    result = asyncio.run(scenario())
    assert "strategy_lab:RuntimeError" in result.failures
    assert result.management == {account.portfolio: "running"}  # management ran in the same tick
