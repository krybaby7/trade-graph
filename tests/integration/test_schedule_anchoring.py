"""Recurring schedules keep their persisted phase; tick latency and restarts never drift it."""

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from tests.integration.test_execution import _stack

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.paper_service import PaperService
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock

NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.fixture
def stack(tmp_path):
    database = Database(tmp_path / "anchoring.sqlite")
    clock = FrozenClock(NOW)
    scheduler = Scheduler(database, clock)
    yield database, clock, scheduler
    database.close()


def _schedule(database, name="trader-review"):
    return database.execute("SELECT * FROM schedules WHERE portfolio_id = 'p' AND name = ?", (name,)).fetchone()


def test_recurring_occurrences_keep_their_persisted_phase_despite_tick_latency(stack):
    database, clock, scheduler = stack
    scheduler.ensure_schedule("p", "trader-review", 14400, "coalesce")
    clock.advance(7)
    assert scheduler.coalesce_due("p", "trader-review", "trader")
    assert _schedule(database)["next_due_at"] == "2026-09-30T04:00:00.000000Z"
    assert _schedule(database)["last_due_at"] == "2026-09-30T00:00:07.000000Z"
    clock.advance(14400 + 2)
    assert scheduler.coalesce_due("p", "trader-review", "trader")
    assert _schedule(database)["next_due_at"] == "2026-09-30T08:00:00.000000Z"
    assert _schedule(database)["last_due_at"] == "2026-09-30T04:00:09.000000Z"


def test_missed_occurrences_coalesce_once_and_resume_on_the_original_grid(stack):
    database, clock, scheduler = stack
    scheduler.ensure_schedule("p", "trader-review", 14400, "coalesce")
    assert scheduler.coalesce_due("p", "trader-review", "trader")
    clock.advance(13.5 * 3600)
    task_id = scheduler.coalesce_due("p", "trader-review", "trader")
    assert task_id
    dedup_key = database.execute("SELECT dedup_key FROM tasks WHERE task_id = ?", (task_id,)).fetchone()[0]
    assert dedup_key.endswith(":2026-09-30T04:00:00.000000Z")
    assert _schedule(database)["next_due_at"] == "2026-09-30T16:00:00.000000Z"
    assert scheduler.coalesce_due("p", "trader-review", "trader") is None
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2


def test_advance_due_moves_only_a_due_schedule_on_its_grid(stack):
    database, clock, scheduler = stack
    assert scheduler.advance_due("p", "trader-review") is False
    scheduler.ensure_schedule("p", "trader-review", 14400, "coalesce")
    clock.advance(5 * 3600 + 3)
    assert scheduler.advance_due("p", "trader-review") is True
    assert _schedule(database)["next_due_at"] == "2026-09-30T08:00:00.000000Z"
    assert _schedule(database)["last_due_at"] == "2026-09-30T05:00:03.000000Z"
    assert scheduler.advance_due("p", "trader-review") is False
    assert _schedule(database)["next_due_at"] == "2026-09-30T08:00:00.000000Z"
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_still_active_occurrence_advances_on_the_grid(tmp_path):
    clock, ledger, execution, _broker, _portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def slow(task):
        entered.set()
        assert finish.wait(5)
        return {}

    service = PaperService(ledger.database, execution, handlers={"research": slow},
                           schedule_intervals={"research": 60}, role_ttl_seconds=100)

    async def scenario():
        await service.tick()
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            clock.advance(70)
            assert (await service.tick()).scheduled == 0
        finally:
            finish.set()
            await service.stop()

    asyncio.run(scenario())
    row = ledger.database.execute("SELECT next_due_at FROM schedules WHERE name = 'research-review'").fetchone()
    assert row["next_due_at"] == "2026-01-01T00:02:00.000000Z"
    assert ledger.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
