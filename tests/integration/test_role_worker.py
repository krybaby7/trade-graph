"""Fenced role worker. No provider or exchange calls."""

from datetime import UTC, datetime

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.clock import FrozenClock


def _stack(tmp_path):
    database = Database(tmp_path / "worker.sqlite")
    clock = FrozenClock(datetime(2026, 9, 30, tzinfo=UTC))
    scheduler = Scheduler(database, clock)
    return database, clock, scheduler


def _handler(events):
    def run(task: dict) -> dict:
        events.append(("effect", task["role"]))
        return {"ok": True}

    return run


def test_reclaimed_work_reconciles_before_another_effect(tmp_path) -> None:
    database, clock, scheduler = _stack(tmp_path)
    events: list = []

    def reconcile() -> None:
        events.append("reconcile")

    worker = RoleWorker(scheduler, owner="worker-a", system_version_id="v1", reconcile=reconcile)
    scheduler.add_task(role="trader", objective="once", portfolio_id="p", payload={"symbol": "BTC/USD"})
    assert worker.run_available({"trader": _handler(events)}) == 1
    assert events == [("effect", "trader")]
    assert database.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"] == 1

    blocked = RoleWorker(scheduler, owner="worker-b", system_version_id="v1", reconcile=reconcile)
    scheduler.add_task(
        role="trader",
        objective="second",
        portfolio_id="p",
        dedup_key="other",
        due_at="2099-01-01T00:00:00+00:00",
    )
    assert blocked.run_available({"trader": _handler(events)}) == 0
    assert events == [("effect", "trader")]

    clock.advance(31)
    scheduler.add_task(role="research", objective="fresh", portfolio_id="p", dedup_key="research")
    lease = scheduler.claim("worker-a")
    assert lease is not None and lease.reclaimed is False
    scheduler.note_attempt(lease)
    clock.advance(31)
    events.clear()
    assert worker.run_available({"research": _handler(events), "trader": _handler(events)}) == 1
    assert events == ["reconcile", ("effect", "research")]


def test_version_mismatch_skips_without_an_effect(tmp_path) -> None:
    database, _clock, scheduler = _stack(tmp_path)
    events: list = []
    worker = RoleWorker(
        scheduler,
        owner="worker-a",
        system_version_id="v1",
        reconcile=lambda: events.append("reconcile"),
    )
    task_id = scheduler.add_task(
        role="trader",
        objective="old",
        portfolio_id="p",
        expected_version="v0",
    )
    assert worker.run_available({"trader": _handler(events)}) == 1
    assert events == []
    row = database.execute(
        "SELECT status, attempts_used, output_json FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "DEAD_LETTER"
    assert row["attempts_used"] == 0
    assert "version" in row["output_json"]
    assert database.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"] == 0
