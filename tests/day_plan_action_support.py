"""Helpers for the Trader's position actions (W8-B, C4): a filled day-plan position, actions and broker views.

Built on the day-plan account (synthetic money limits, the example practice strategy with one synthetic confirmation
report, USD10,000, SOL near 100). No network, model, key or real order.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from tests.day_plan_support import broker_orders, install, plan
from tests.money_limit_support import Account
from trade_graph.application.day_plan_actions import ActionRecord, DayPlanActions
from trade_graph.contracts.day_plans import DayPlan, PositionAction

D = Decimal


def fill_quote(account: Account, *, bid: str = "99.4", ask: str = "99.5", volume: str | None = "100",
               symbol: str = "SOL/USD") -> list[str]:
    """One second later, a synthetic quote; protective exits recorded with its fills are sent at once."""
    account.clock.advance(1)
    fills = account.quote(symbol, bid, ask, volume=volume)
    asyncio.run(account.execution.dispatch(protection_only=True))
    return fills


def filled(account: Account, day_plan: DayPlan | None = None, *, request_id: str = "plan-1", **quote: Any):
    """Install a plan, send its entries and fill them with one quote; the exits are at the broker afterwards."""
    installed = install(account, day_plan or plan(), request_id=request_id)
    account.dispatch()
    fill_quote(account, **quote)
    return installed


def position_action(action: str = "move_exit", symbol: str = "SOL/USD", **fields: Any) -> PositionAction:
    document: dict[str, Any] = {"action": action, "symbol": symbol,
                                "reason": f"Synthetic: {action.replace('_', ' ')} on {symbol}."}
    if action == "move_exit":
        document.update({"stop_price": "99", "take_profit_price": "106"})
    document.update(fields)
    return PositionAction.model_validate(document)


def act(account: Account, action: PositionAction | None = None, *, request_id: str = "action-1",
        actions: DayPlanActions | None = None, task_id: str | None = None) -> ActionRecord:
    actions = actions or DayPlanActions(account.execution)
    return asyncio.run(actions.apply(account.portfolio, action or position_action(), request_id=request_id,
                                     task_id=task_id))


def steps(record: ActionRecord) -> list[str]:
    return [step["kind"] for step in record.steps]


def live_exits(account: Account, symbol: str = "SOL/USD") -> list[tuple[Decimal, Decimal | None, Decimal]]:
    """Open sell stops at the broker: (stop, take-profit, what remains to sell), oldest first."""
    return [(D(order["stop_price"]), None if order.get("take_profit_price") is None else D(order["take_profit_price"]),
             D(order["remaining"]))
            for order in broker_orders(account, side="sell", live=True)
            if order["order_type"] == "stop" and order["symbol"] == symbol]


def market_sells(account: Account, symbol: str = "SOL/USD") -> list[dict]:
    return [order for order in broker_orders(account, side="sell")
            if order["order_type"] == "market" and order["symbol"] == symbol]


__all__ = ["act", "fill_quote", "filled", "live_exits", "market_sells", "plan", "position_action", "steps"]
