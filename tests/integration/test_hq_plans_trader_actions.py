"""HQ's day plans panel with the Trader's exit tools (wave 8 integration of W8-B and W8-C).

W8-C (D251) reads what a filled buy has without a safety exit with ``DayPlanProtection.coverage``'s arithmetic, and
W8-B (D246) made a close's market sell part of that arithmetic (``day_plan_action_orders``). Built side by side, the
merged panel told the owner a position the Trader had closed (sold, or being sold) had "no safety exit right now".
These tests pin the merged behaviour: a close's sell covers each entry's part while it is outstanding or filled and
only what it sold once it ended unfilled; the sell is shown under its buy; buys and safety exits the Trader's actions
cancelled say why.

Synthetic paper account, synthetic money limits, scripted quotes and brokers (``tests/day_plan_support.py``,
``tests/day_plan_action_support.py``); no network, key, model, provider or exchange call. Reading starts nothing.
"""

from __future__ import annotations

from decimal import Decimal

from tests.day_plan_action_support import act, fill_quote, filled, position_action
from tests.day_plan_support import day_plan_account, entry, install, plan
from tests.hq_trading_support import runtime_for

from trade_graph.api import hq_copy, hq_plans
from trade_graph.api.hq_plans_contract import validate_plans
from trade_graph.contracts.models import SubmitResult

D = Decimal


def _read(account) -> dict:
    document = hq_plans.plans(runtime_for(account))
    validate_plans(document)
    return document


def _text(value) -> str:
    return hq_copy.plain(value)


def _entries(account) -> list[dict]:
    (item,) = _read(account)["plans"]
    return item["entries"]


class _RefusesMarketSells:
    """The paper broker, except that it refuses market sells (the close's sell), as a venue might."""

    def __init__(self, inner) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def submit(self, intent):
        if intent.order_type == "market":
            return SubmitResult(status="rejected", error="rejected", message="synthetic venue refusal")
        return await self.inner.submit(intent)


def test_a_closed_position_is_never_shown_as_without_a_safety_exit(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    record = act(account, position_action("close"))
    assert record.status == "done"
    # The close's market sell is out: it covers the 5 SOL it sells, as it does for the protection sweep.
    (bought,) = _entries(account)
    assert (bought["status"], bought["filled"], bought["unprotected"]) == ("filled", "5", "0")
    assert bought["tone"] != "attention" and "no safety exit" not in _text(bought["text"])
    old, sell = bought["exits"]
    assert old["status"] == "cancelled"
    assert "cancelled at the Trader's request to close the position" in _text(old["text"])
    assert sell["status"] in ("sending", "waiting")
    assert _text(sell["text"]).startswith("The Trader's close: sell 5 SOL at the market price")
    (plan_view,) = _read(account)["plans"]
    assert "without a safety exit" not in _text(plan_view["headline"])
    # The sell fills at the next quote: sold, with its fill, and still nothing reads as unprotected.
    fill_quote(account, bid="99.6", ask="99.7", volume=None)
    (bought,) = _entries(account)
    assert bought["unprotected"] == "0" and bought["tone"] == "good"
    sell = bought["exits"][-1]
    assert sell["status"] == "filled" and _text(sell["text"]).endswith(": sold.")
    assert len(sell["fills"]) == 1 and _text(sell["fills"][0]).startswith("Sold 5 SOL at")


def test_a_buy_the_close_cancelled_says_why(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account, plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "98", "95.5")]))
    account.dispatch()
    fill_quote(account)  # the 99.5 buy fills; the 98 buy keeps waiting
    assert act(account, position_action("close")).status == "done"
    bought, resting = _entries(account)
    assert bought["unprotected"] == "0"
    assert resting["status"] == "cancelled" and resting["unprotected"] == "0"
    assert "cancelled because the Trader closed the position" in _text(resting["text"])


def test_a_moved_exit_says_why_the_old_one_was_cancelled(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    assert act(account, position_action("move_exit", stop_price="99", take_profit_price="106")).status == "done"
    (bought,) = _entries(account)
    assert bought["unprotected"] == "0"
    old, moved = bought["exits"]
    assert "cancelled at the Trader's request to move the safety exit" in _text(old["text"])
    assert moved["status"] == "waiting" and "stop 99, or target 106" in _text(moved["text"])


def test_a_close_whose_sell_was_refused_counts_the_exit_put_back_and_not_the_refused_sell(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    account.execution.broker = _RefusesMarketSells(account.execution.broker)
    record = act(account, position_action("close"))
    assert record.status == "refused"
    (bought,) = _entries(account)
    old, *rest = bought["exits"]
    sells = [item for item in rest if _text(item["text"]).startswith("The Trader's close")]
    back = [item for item in rest if not _text(item["text"]).startswith("The Trader's close")]
    assert [item["status"] for item in sells] == ["rejected"]
    assert [item["status"] for item in back] == ["waiting"]  # the old exit, put back with its own terms
    assert "stop 97, or target 104" in _text(back[0]["text"])
    # Covered by the exit put back, not by the refused sell: nothing reads as unprotected, nothing is counted twice.
    assert bought["unprotected"] == "0"
