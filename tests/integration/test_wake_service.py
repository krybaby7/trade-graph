"""The paper service with the event engine (T35): wakes replace the fixed role timers, the allowance floor holds new
AI work while management and protection go on, and an obsolete wake task never runs. Synthetic account, scripted
feed and departments; no network, model, key or real order.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.day_plan_support import day_plan_account, entry, install, plan, protected_quantity
from tests.wake_support import scripted, wake_input

from trade_graph.application.events import EngineOptions
from trade_graph.application.paper_service import PaperService
from trade_graph.contracts.models import Observation
from trade_graph.domain.errors import ValidationFailure

D = Decimal
JUST_AFTER = datetime(2026, 10, 16, 0, 0, 30, tzinfo=UTC)


class Feed:
    """A scripted public feed: each poll returns the next batch of synthetic quotes."""

    def __init__(self, account) -> None:
        self.account, self.batches = account, []

    def quote(self, bid: str, ask: str, *, volume: str | None = None, symbol: str = "SOL/USD") -> None:
        self.batches.append([Observation(
            observation_id=f"feed-{len(self.batches)}", venue="paper", symbol=symbol,
            event_time_utc=self.account.clock.now(), available_at_utc=self.account.clock.now(), bid=bid, ask=ask,
            bid_size="1000", ask_size="1000", volume=volume, kind="quote", source="synthetic-wake-service")])

    def poll(self):
        return self.batches.pop(0) if self.batches else []

    def close(self) -> None:
        pass


def _service(account, handlers, *, feed=None, options=None, **kwargs) -> PaperService:
    return PaperService(account.database, account.execution, clock=account.clock, portfolio_ids=[account.portfolio],
                        handlers=handlers, public_feed=feed, event_engine=options or EngineOptions(), **kwargs)


def test_with_the_event_engine_wakes_replace_the_fixed_role_timers(tmp_path) -> None:
    account = day_plan_account(tmp_path, now=JUST_AFTER)
    seen: list = []
    service = _service(account, {"research": scripted(seen=seen), "trader": scripted(seen=seen),
                                 "leader": scripted(seen=seen)})

    async def scenario():
        for _ in range(4):
            await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    assert account.database.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] == 0  # no fixed timers
    objectives = [row[0] for row in account.database.execute("SELECT objective FROM tasks ORDER BY rowid")]
    assert objectives and all(objective.startswith("wake:") for objective in objectives)
    assert {task["role"] for task in seen} == {"research", "trader", "leader"}
    assert [wake_input(task)["purpose"] for task in seen if task["role"] == "trader"] == ["day_plan"]
    allocated = account.database.execute("SELECT allocated_spend, max_attempts FROM tasks").fetchall()
    policy = account.execution.authority.active_policy()
    assert {(row[0], row[1]) for row in allocated} == {(str(policy.root_paid_limit.amount),
                                                        policy.ordinary_max_paid_attempts)}


def test_without_the_event_engine_the_service_keeps_its_fixed_schedules(tmp_path) -> None:
    account = day_plan_account(tmp_path, now=JUST_AFTER)
    service = PaperService(account.database, account.execution, clock=account.clock,
                           portfolio_ids=[account.portfolio], handlers={"trader": scripted()})

    async def scenario():
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    assert account.database.execute("SELECT name FROM schedules").fetchone()[0] == "trader-review"
    assert account.database.execute("SELECT COUNT(*) FROM wakes").fetchone()[0] == 0


def test_the_allowance_floor_holds_new_ai_work_while_management_and_protection_continue(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account, plan(entries=[entry("5", "99.5", "97", "104")]))
    account.database.execute(
        "INSERT INTO subscription_provider_state (provider, ai_paused, reason, quota_json, updated_at) "
        "VALUES ('codex_subscription', 1, 'allowance floor', '{}', ?)", (account.clock.now().isoformat(),))
    feed = Feed(account)
    seen: list = []
    service = _service(account, {"trader": scripted(seen=seen)}, feed=feed,
                       options=EngineOptions(daily_cycle=False))

    async def scenario():
        await service.tick(wait_feed=True, wait_roles=True)  # sends the resting entry
        account.clock.advance(1)
        feed.quote("99.4", "99.5", volume="100")  # fills it; its protective exit goes out at once
        await service.tick(wait_feed=True, wait_roles=True)
        assert seen == []  # no AI work under the floor
        assert protected_quantity(account) == D("5") and account.held("SOL") == D("5")
        (wake,) = service.event_engine.wakes(account.portfolio)
        assert wake.reasons == ("entry_filled",) and wake.allowance.status == "paused"
        account.database.execute("UPDATE subscription_provider_state SET ai_paused = 0")
        account.clock.advance(1)
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    assert [wake_input(task)["reasons"] for task in seen] == [["entry_filled"]]
    assert service.event_engine.wake(seen[0]["input"]["wake"]["wake_id"]).status == "completed"


def test_the_service_never_runs_a_wake_task_that_became_obsolete(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    seen: list = []
    service = _service(account, {"trader": scripted({"day_plans": [plan().model_dump(mode="json")]}, seen=seen)})

    async def scenario():
        await service.start()
        await asyncio.to_thread(service._thread_call, service._schedules)  # the day start queues the day plan
        account.clock.advance(timedelta(hours=17).total_seconds())  # it is tomorrow before the role runner looks
        assert await asyncio.to_thread(service._thread_call, service._run_role) == 0
        await service.stop()

    asyncio.run(scenario())
    assert seen == []
    (wake,) = service.event_engine.wakes(account.portfolio)
    assert wake.status == "obsolete"
    assert account.database.execute("SELECT status FROM tasks").fetchone()[0] == "DEAD_LETTER"
    assert account.database.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0] == 0


def test_the_event_engine_option_is_typed(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    with pytest.raises(ValidationFailure, match="EngineOptions"):
        PaperService(account.database, account.execution, clock=account.clock, event_engine=True)
