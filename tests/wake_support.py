"""Helpers for the event-engine tests (T35): a synthetic paper account, an engine and scripted departments.

Built on the day-plan account (synthetic money limits, the example practice strategy with one synthetic confirmation
report, USD10,000, SOL near 100). Departments are plain scripted handlers; no network, model, key or real order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from tests.day_plan_support import day_plan_account, plan
from trade_graph.application.events import EventEngine
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.worker import RoleWorker

ALL = ("trader", "research", "leader", "learning", "optimisation")


def engine_for(account, *, departments: Iterable[str] = ALL, **options: Any) -> tuple[Scheduler, EventEngine]:
    scheduler = Scheduler(account.database, account.clock)
    return scheduler, EventEngine(account.execution, scheduler, departments=departments, **options)


def engine_account(tmp_path, *, departments: Iterable[str] = ALL, **options: Any):
    account = day_plan_account(tmp_path)
    scheduler, engine = engine_for(account, departments=departments, **options)
    return account, scheduler, engine


def wake_input(task: Mapping[str, Any]) -> dict[str, Any]:
    return task["input"]["wake"]


def scripted(outcome: dict[str, Any] | Callable[[dict], dict] | None = None, *, seen: list | None = None,
             fail: bool = False) -> Callable[[dict], dict]:
    """A scripted department: records what it was handed and answers with ``outcome`` under ``wake_outcome``."""

    def handler(task: dict) -> dict:
        if seen is not None:
            seen.append(task)
        if fail:
            return {"_status": "FAILED", "reason": "synthetic failed turn", "failure_scope": "turn"}
        answer = outcome(task) if callable(outcome) else outcome
        return {} if answer is None else {"wake_outcome": answer}

    return handler


def run_one(scheduler: Scheduler, engine: EventEngine, handlers: Mapping[str, Callable[[dict], dict]], *,
            owner: str = "test-worker") -> str | None:
    """Claim and run one due task of the given departments through the real worker; returns its task id."""
    lease = scheduler.claim(owner, roles=set(handlers))
    if lease is None:
        return None
    row = scheduler.leased_row(lease)
    verdict = engine.before_run(row, reclaimed=lease.reclaimed)
    if verdict is not None:
        scheduler.skip(lease, {"reason": verdict, "obsolete_wake": True})
    else:
        RoleWorker(scheduler, owner=owner, system_version_id="wake-tests",
                   reconcile=lambda: None)._run_lease(lease, dict(handlers))
    engine.finish_task(lease.task_id)
    return lease.task_id


def run_all(scheduler: Scheduler, engine: EventEngine, handlers: Mapping[str, Callable[[dict], dict]],
            portfolio_ids: Iterable[str], *, limit: int = 50) -> list[str]:
    """Step the engine and run due tasks until none is left (``limit`` guards the test, not the engine)."""
    ran = []
    for _ in range(limit):
        engine.step(list(portfolio_ids))
        task_id = run_one(scheduler, engine, handlers)
        if task_id is None:
            engine.step(list(portfolio_ids))
            if run_one(scheduler, engine, handlers) is None:
                return ran
            continue
        ran.append(task_id)
    raise AssertionError("tasks kept appearing")


def tasks(account, role: str | None = None) -> list[dict[str, Any]]:
    rows = account.database.execute(
        "SELECT * FROM tasks WHERE (? IS NULL OR role = ?) ORDER BY rowid", (role, role)).fetchall()
    return [dict(row) for row in rows]


__all__ = ["ALL", "engine_account", "engine_for", "plan", "run_all", "run_one", "scripted", "tasks", "wake_input"]
