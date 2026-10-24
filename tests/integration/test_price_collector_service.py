"""The paper service runs the price collector in its own thread (W6-D, D222): management never waits for it, a failed
step is reported in the tick, and a stop ends the collector's waiting at once. Synthetic account; no network."""

from __future__ import annotations

import asyncio
import threading

from tests.day_plan_support import day_plan_account

from trade_graph.application.paper_service import PaperService
from trade_graph.application.price_collector import StepReport


class SlowCollector:
    """Its step blocks until released or stopped, like a chunk waiting for the limiter."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        self.steps = 0
        self.resumed = 0
        self.fail_next = False

    def step(self) -> StepReport:
        self.steps += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic collector failure")
        self.started.set()
        while not (self.release.is_set() or self.stopped.is_set()):
            self.release.wait(0.01)
        return StepReport("stopped" if self.stopped.is_set() else "candles")

    def request_stop(self) -> None:
        self.stopped.set()

    def resume(self) -> None:
        self.resumed += 1
        self.stopped.clear()


def _service(account, collector) -> PaperService:
    return PaperService(account.database, account.execution, clock=account.clock, portfolio_ids=[account.portfolio],
                        handlers={}, tick_interval_seconds=0.01, price_collector=collector)


def test_a_tick_never_waits_for_the_collector_and_a_stop_ends_its_wait(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    collector = SlowCollector()
    service = _service(account, collector)

    async def scenario():
        first = await service.tick()
        assert collector.started.wait(5)
        second = await service.tick()  # the first step still waits; management ran again
        assert second.management is not None and collector.steps == 1
        await service.stop()
        return first, second

    first, second = asyncio.run(scenario())
    assert collector.stopped.is_set() and collector.resumed == 1
    assert not [failure for failure in first.failures + second.failures if failure.startswith("collector")]


def test_a_failed_collector_step_is_reported_and_the_next_tick_starts_another(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    collector = SlowCollector()
    collector.fail_next = True
    collector.release.set()
    service = _service(account, collector)

    async def scenario():
        failures = []
        for _ in range(6):
            result = await service.tick()
            failures.extend(result.failures)
            await asyncio.sleep(0.02)
        await service.stop()
        return failures

    failures = asyncio.run(scenario())
    assert "collector:RuntimeError" in failures
    assert collector.steps >= 2
