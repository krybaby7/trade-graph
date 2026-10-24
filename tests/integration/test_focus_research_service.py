"""Research's focus workflow wired into the paper service's daily cycle (T35, T37): scan -> Research focus on
Perplexity -> Trader day plans. A department switched to Perplexity is billed per call, so its wake tasks carry the
owner's paid allocation even when the service runs on a subscription.

Synthetic account, recorded synthetic scan, scripted Perplexity transport and a scripted Trader; no network, model,
real key, paid call or real order.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.focus_research_support import (
    BTC_NEWS,
    SOL_NEWS,
    final,
    finding,
    focus_answer,
    prompt,
    ref_for,
    research_setup,
    search,
    switch,
    tool_call,
)
from tests.wake_support import scripted, wake_input

from trade_graph.application.events import EngineOptions
from trade_graph.application.paper_service import PaperService


def _answer(body: dict) -> dict:
    sol = ref_for(body, SOL_NEWS["url"])
    return focus_answer([("SOL/USD", "Synthetic: an upgrade this week.", [sol, prompt(body)["scan"]["scan_id"]])],
                        findings=[finding(sol, "SOL/USD")], summary="SOL today.")


def test_the_daily_cycle_runs_the_scan_research_focus_on_perplexity_and_then_the_trader(tmp_path) -> None:
    research = research_setup(tmp_path, [tool_call("Solana news", "Bitcoin news"), search(SOL_NEWS),
                                         search(BTC_NEWS), final(_answer)])
    account = research.account
    seen: list = []
    service = PaperService(account.database, account.execution, clock=account.clock,
                           portfolio_ids=[account.portfolio], event_engine=EngineOptions(),
                           handlers={"research": research.handler, "trader": scripted(seen=seen)})

    async def morning():
        for _ in range(4):
            account.clock.advance(1)
            result = await service.tick(wait_roles=True)
            assert not [failure for failure in result.failures if failure.startswith("wakes")], result.failures
        await service.stop()

    asyncio.run(morning())
    engine = service.event_engine
    steps = engine.cycle(account.portfolio)
    assert {name: item["status"] for name, item in steps.items()} == {
        "day_start": "done", "scan": "done", "focus": "done", "day_plan": "done"}
    assert engine.focus(account.portfolio).selection.symbols() == ("SOL/USD",)
    (trader,) = [wake_input(task) for task in seen]
    assert trader["purpose"] == "day_plan" and trader["focus"]["selection"]["coins"][0]["symbol"] == "SOL/USD"
    assert trader["focus_step"]["status"] == "done"
    assert len(research.transport.calls) == 4 and len(research.receipts()) == 4


def test_a_department_switched_to_perplexity_gets_the_paid_allocation_under_a_subscription(tmp_path) -> None:
    research = research_setup(tmp_path, switched_on=False)
    account = research.account
    service = PaperService(account.database, account.execution, clock=account.clock,
                           portfolio_ids=[account.portfolio], event_engine=EngineOptions(),
                           subscription_provider="codex_subscription",
                           handlers={"research": research.handler, "leader": scripted(), "trader": scripted()})
    root = account.execution.authority.active_policy().root_paid_limit.amount
    off = {role: service._wake_task(account.portfolio, role).allocated_spend for role in ("research", "leader",
                                                                                           "trader")}
    assert off == {"research": Decimal("0"), "leader": Decimal("0"), "trader": Decimal("0")}  # subscription work
    switch(account, research=True, leader=True)
    on = {role: service._wake_task(account.portfolio, role).allocated_spend for role in ("research", "leader",
                                                                                          "trader")}
    assert on == {"research": root, "leader": root, "trader": Decimal("0")}  # the Trader stays on its subscription
