"""A synthetic paper account for the day-plan tests (T34). No network, model, key or real order.

Built on the money-limit account (synthetic limits: 4% a day, 30% per coin, 90% in total; USD10,000; SOL near 100,
BTC near 50,000) with the example practice strategy registered and one synthetic confirmation report, so plans can
be installed. ``plan()`` writes a small SOL plan; tests change what they need.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.money_limit_support import Account, open_account
from trade_graph.application.day_plans import DayPlans
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.day_plans import DayPlan
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow, StudyPlan

D = Decimal
ROOT = Path(__file__).resolve().parents[1]
STRATEGY = PracticeStrategy.model_validate_json(
    (ROOT / "strategies" / "practice" / "slow-trend-pullback.v1.json").read_text())


def register_strategy(account: Account, strategy: PracticeStrategy = STRATEGY) -> PracticeRangeStore:
    store = PracticeRangeStore(account.database, account.clock)
    store.register(strategy)
    return store


def record_report(account: Account, *, phase: str = "confirm", strategy: PracticeStrategy = STRATEGY,
                  symbol: str = "SOL/USD", study_id: str | None = None) -> str:
    """One synthetic practice-range report (labelled synthetic; nothing was replayed)."""
    store = register_strategy(account, strategy)
    study = StudyPlan(study_id=study_id or f"{strategy.strategy_id}-dp", strategy_id=strategy.strategy_id,
                      tune=ReplayWindow(start=date(2026, 6, 1), end=date(2026, 8, 1)),
                      confirm=ReplayWindow(start=date(2026, 8, 1), end=date(2026, 9, 1)))
    store.create_study(study)
    run = store.record_run(
        study_id=study.study_id, phase=phase, strategy_ref=strategy.ref, symbol=symbol, interval_minutes=15,
        source="synthetic", cost_profile_ref="kraken-spot-starting-tier@1", fill_policy_ref="bar-replay@1",
        window=study.window(phase) if phase == "confirm" else study.tune, data_as_of="2026-10-01T00:00:00Z",
        evidence=(), result_sha256="0" * 64, report={"label": "synthetic day-plan test report"})
    return run.run_id


def day_plan_account(tmp_path, *, report: bool = True, **options) -> Account:
    account = open_account(tmp_path, **options)
    if report:
        record_report(account)
    return account


def plan(**changes: Any) -> DayPlan:
    document: dict[str, Any] = {
        "symbol": "SOL/USD",
        "strategy_ref": STRATEGY.ref,
        "indicators": [{"indicator": "RSI", "interval_minutes": 15}],
        "alarms": [{"kind": "quote_price", "symbol": "SOL/USD", "relation": "crosses_below", "level": "95",
                    "note": "the pullback is deeper than planned"}],
        "entries": [{"quantity": "5", "limit_price": "99.5", "stop_price": "97", "take_profit_price": "104"}],
        "rationale": "Synthetic plan: rest a bid under the market with a stop and a target.",
        "invalidation": "Synthetic: a quote below 95.",
    }
    document.update(changes)
    return DayPlan.model_validate(document)


def entry(quantity: str, limit: str, stop: str, target: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"quantity": quantity, "limit_price": limit, "stop_price": stop, "take_profit_price": target, **extra}


def install(account: Account, day_plan: DayPlan | None = None, *, request_id: str = "plan-1",
            plans: DayPlans | None = None, **options: Any):
    plans = plans or DayPlans(account.execution)
    return asyncio.run(plans.install(account.portfolio, day_plan or plan(), request_id=request_id, **options))


def manage(account: Account, plans: DayPlans | None = None) -> dict:
    return asyncio.run((plans or DayPlans(account.execution)).manage(account.portfolio))


def broker_orders(account: Account, *, side: str | None = None, live: bool = False) -> list[dict]:
    import json

    rows = account.database.execute("SELECT client_order_id, status, document_json FROM broker_orders "
                                    "ORDER BY rowid").fetchall()
    orders = []
    for row in rows:
        document = json.loads(row["document_json"])
        if side is not None and document["side"] != side:
            continue
        if live and row["status"] not in {"open", "partially_filled"}:
            continue
        orders.append({"client_order_id": row["client_order_id"], "status": row["status"], **document})
    return orders


def protected_quantity(account: Account, symbol: str = "SOL/USD") -> Decimal:
    """What live protective exits at the broker would sell: the remainder of every open sell stop."""
    return sum((D(order["remaining"]) for order in broker_orders(account, side="sell", live=True)
                if order["order_type"] == "stop" and order["symbol"] == symbol), D("0"))


__all__ = ["ROOT", "STRATEGY", "broker_orders", "day_plan_account", "entry", "install", "manage", "plan",
           "protected_quantity", "record_report", "register_strategy"]
