"""The Trader's exit tools never leave a day-plan position without its safety exit (W8-B, C4 acceptance).

Random sequences of quotes (whole and partial fills of resting entries, stops and take-profits), the Trader closing
its position or moving its exit (valid and refused terms), broker faults (a cancel that gets no answer, a refused
market sell, a refused or unacknowledged new stop), crashes in the middle of an action and its retry, pauses,
restarts (new objects on the same database file, then the startup reconciliation) and new plans. Invariants:

- spot only: nothing is ever reserved or offered at the broker beyond what is held;
- whenever the pause keeps protection, after every management step the records cover exactly what is held: the
  outstanding exits, plus a close's sell while it is still out ("except during the sell itself");
- right after an action that ended (``done``, ``refused`` or ``failed``) the same holds without any management step:
  a refused or unknown step left the old exits in place;
- an action never places a second sell, and no entry reaches the broker twice.

Synthetic account (USD10,000; 4% a day, 30% per coin, 90% in total), scripted quotes and brokers; no network, model
or key.
"""

from __future__ import annotations

import asyncio
import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.day_plan_action_support import position_action
from tests.day_plan_support import broker_orders, day_plan_account, entry, install, plan, protected_quantity
from tests.money_limit_support import Account

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.day_plan_actions import DayPlanActions
from trade_graph.application.day_plan_protection import OUTSTANDING
from trade_graph.application.day_plans import DayPlans
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import CancelResult, SubmitResult
from trade_graph.domain.errors import TradeGraphError, UncertainExternal
from trade_graph.kernel.authority import pause_allows_protection

D = Decimal
ASKS = ["101", "100.1", "99.5", "99.2", "98", "97.6", "97", "96", "95.5", "94", "93", "101.5", "102", "104.5"]
VOLUMES = [None, "0.0003", "1.2345", "2", "100"]
PAUSES = ["MANAGE_ONLY", "NO_NEW_EXPOSURE", "PAUSE_DECISIONS", "CANCEL_ALL"]
STOPS = ["94.5", "96.5", "98", "99.2"]
TARGETS = [None, "103", "106"]
FAULTS = ["cancel_unanswered", "refuse_market", "lose_stop_ack"]
# Only a stop at this price is refused (in a whole example, so a move to it never succeeds and never becomes the
# terms in force) or loses its acknowledgement: the terms the Trader wrote always work at the broker.
FAULTY_STOP = D("99.2")

step = st.one_of(
    st.tuples(st.just("quote"), st.sampled_from(ASKS), st.sampled_from(VOLUMES)),
    st.tuples(st.just("close")),
    st.tuples(st.just("move"), st.sampled_from(STOPS), st.sampled_from(TARGETS)),
    st.tuples(st.just("fault"), st.sampled_from(FAULTS)),
    st.tuples(st.just("crash"), st.sampled_from(["close", "move"]), st.sampled_from(["after_cancel", "before_send"])),
    st.tuples(st.just("retry")),
    st.tuples(st.just("pause"), st.sampled_from(PAUSES)),
    st.tuples(st.just("resume")),
    st.tuples(st.just("later"), st.sampled_from([60, 900])),
    st.tuples(st.just("replan")),
    st.tuples(st.just("restart")),
)


class _Faulty:
    """The paper broker with switchable faults (all off after a restart, except a refused stop)."""

    def __init__(self, inner: PaperBroker, *, refuse_stop: bool) -> None:
        self.inner, self.faults = inner, {"refuse_stop"} if refuse_stop else set()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def cancel(self, request):
        if "cancel_unanswered" in self.faults:
            return CancelResult(status="uncertain", error="timeout_uncertain", message="synthetic: no answer")
        return await self.inner.cancel(request)

    async def submit(self, intent):
        if "refuse_market" in self.faults and intent.order_type == "market":
            return SubmitResult(status="rejected", error="rejected", message="synthetic venue refusal")
        if "refuse_stop" in self.faults and intent.stop_price == FAULTY_STOP:
            return SubmitResult(status="rejected", error="rejected", message="synthetic venue refusal")
        result = await self.inner.submit(intent)
        if "lose_stop_ack" in self.faults and intent.stop_price == FAULTY_STOP:
            raise UncertainExternal("synthetic: acknowledgement lost")
        return result


class _Crash(Exception):
    """Not a TradeGraphError: the process stops in the middle of an action."""


def _restart(account: Account) -> Account:
    database = Database(account.database.path)
    ledger = Ledger(database, account.clock)
    broker = PaperBroker(database, account.clock)
    execution = Execution(database, ledger, account.clock, broker)
    restarted = Account(account.clock, database, ledger, broker, execution, account.portfolio, account._sequence)
    asyncio.run(restarted.execution.startup())
    return restarted


def _outstanding_remaining(account: Account, intents) -> Decimal:
    total = D("0")
    for intent_id in intents:
        if account.state(intent_id) in OUTSTANDING:
            execution = account.execution
            total += D(execution._payload(intent_id)["quantity"]) - execution._filled_quantity(intent_id)
    return total


def _records_cover(account: Account) -> Decimal:
    exits = [row["intent_id"] for row in account.database.execute("SELECT intent_id FROM day_plan_exits")]
    sells = [row["intent_id"] for row in account.database.execute(
        "SELECT DISTINCT intent_id FROM day_plan_action_orders WHERE role = 'sell'")]
    return _outstanding_remaining(account, exits) + _outstanding_remaining(account, sells)


def _check(account: Account, *, settled: bool) -> None:
    held = account.held("SOL")
    reserved = account.execution._reserved(account.portfolio, "SOL")
    assert held >= 0 and reserved <= held  # spot only: nothing reserved beyond what is held
    offered = protected_quantity(account) + sum(
        (D(order["remaining"]) for order in broker_orders(account, side="sell", live=True)
         if order["order_type"] == "market"), D("0"))
    assert offered <= held  # the broker is never asked to sell more than is held
    if settled and pause_allows_protection(account.execution.profile(account.portfolio)):
        assert _records_cover(account) == held
    sells = account.database.execute("SELECT action_id, COUNT(DISTINCT intent_id) AS n FROM day_plan_action_orders "
                                     "WHERE role = 'sell' GROUP BY action_id").fetchall()
    assert all(row["n"] == 1 for row in sells)  # one sell per action, ever
    buys = broker_orders(account, side="buy")
    assert len({order["intent_id"] for order in buys}) == len(buys)  # no entry reaches the broker twice


@settings(max_examples=70, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(steps=st.lists(step, min_size=1, max_size=24), refuse_stop=st.booleans(),
       first_fill=st.sampled_from([None, "100", "2"]))
# Explicit sequences for every fault path, so each is exercised whatever the search finds.
@example(steps=[("fault", "cancel_unanswered"), ("close",), ("fault", "cancel_unanswered"), ("quote", "96", None),
                ("close",), ("quote", "99.5", None)], refuse_stop=False, first_fill="100")
@example(steps=[("quote", "99.5", None), ("fault", "cancel_unanswered"), ("move", "98", "106"),
                ("restart",), ("move", "98", "106")], refuse_stop=False, first_fill=None)
@example(steps=[("fault", "refuse_market"), ("close",), ("quote", "97", "2"), ("fault", "refuse_market"),
                ("close",), ("quote", "99", None)], refuse_stop=False, first_fill="100")
@example(steps=[("move", "99.2", "106"), ("quote", "99", None), ("move", "98", None), ("quote", "97.6", None)],
         refuse_stop=True, first_fill="2")
@example(steps=[("fault", "lose_stop_ack"), ("move", "99.2", None), ("quote", "99.1", None), ("restart",),
                ("close",)], refuse_stop=False, first_fill="100")
@example(steps=[("crash", "close", "after_cancel"), ("quote", "96", None), ("retry",), ("quote", "99", None)],
         refuse_stop=False, first_fill="100")
@example(steps=[("crash", "close", "before_send"), ("restart",), ("retry",), ("quote", "99", None)],
         refuse_stop=False, first_fill="100")
@example(steps=[("crash", "move", "after_cancel"), ("restart",), ("retry",), ("quote", "96", None)],
         refuse_stop=False, first_fill="2")
@example(steps=[("crash", "move", "before_send"), ("quote", "99", "2"), ("retry",), ("close",)],
         refuse_stop=False, first_fill=None)
@example(steps=[("crash", "close", "after_cancel"), ("pause", "CANCEL_ALL"), ("resume",), ("move", "98", "103"),
                ("replan",), ("quote", "99", "100"), ("close",)], refuse_stop=False, first_fill="100")
def test_a_day_plan_position_keeps_its_safety_exit_through_closes_moves_faults_and_restarts(steps, refuse_stop,
                                                                                           first_fill) -> None:
    with tempfile.TemporaryDirectory() as folder:
        account = day_plan_account(Path(folder))
        install(account, plan(entries=[
            entry("5", "99.5", "97", "104"),
            entry("3", "98", "95.5"),
            entry("4", "96", "94", "101", expires_at_utc="2026-10-16T09:30:00Z"),
        ]))
        account.dispatch()
        account.clock.advance(1)
        account.quote("SOL/USD", "99.4", "99.5", volume=first_fill)  # the 99.5 entry fills, whole or in part
        asyncio.run(account.execution.dispatch(protection_only=True))
        faulty = _Faulty(account.broker, refuse_stop=refuse_stop)
        account.execution.broker = faulty
        plans, actions = DayPlans(account.execution), DayPlanActions(account.execution)
        retry: tuple | None = None
        number = 0
        try:
            for item in steps:
                kind = item[0]
                number += 1
                if kind == "quote":
                    ask = D(item[1])
                    account.clock.advance(1)
                    account.quote("SOL/USD", str(ask - D("0.1")), str(ask), volume=item[2])
                    asyncio.run(account.execution.dispatch(protection_only=True))
                    _check(account, settled=False)
                elif kind in {"close", "move", "retry"}:
                    if kind == "retry":
                        if retry is None:
                            continue
                        request_id, action = retry
                    else:
                        request_id = f"act-{number}"
                        action = (position_action("close") if kind == "close" else
                                  position_action("move_exit", stop_price=item[1], take_profit_price=item[2]))
                    record = asyncio.run(actions.apply(account.portfolio, action, request_id=request_id))
                    assert record.status in {"done", "refused", "failed"}
                    retry = None
                    _check(account, settled=True)
                elif kind == "crash":
                    request_id = f"act-{number}"
                    action = (position_action("close") if item[1] == "close" else
                              position_action("move_exit", stop_price="96.5", take_profit_price="106"))
                    target, name = ((account.execution, "dispatch") if item[2] == "before_send" else
                                    (account.execution, "authorize") if item[1] == "close" else
                                    (account.execution.day_plan_protection, "place"))
                    original = getattr(target, name)

                    async def stop_async(*_args, **options):
                        if options.get("intent_ids") or options.get("protection_only"):
                            raise _Crash()
                        return await original(*_args, **options)

                    def stop_sync(*_args, **_options):
                        raise _Crash()

                    setattr(target, name, stop_async if name == "dispatch" else stop_sync)
                    try:
                        record = asyncio.run(actions.apply(account.portfolio, action, request_id=request_id))
                        assert record.status in {"done", "refused", "failed"}  # it ended before reaching the crash
                    except _Crash:
                        retry = (request_id, action)  # the engine re-applies the saved answer later
                    finally:
                        delattr(target, name)
                elif kind == "fault":
                    faulty.faults ^= {item[1]}
                elif kind == "pause":
                    account.execution.set_pause(account.portfolio, item[1], "owner", "synthetic pause")
                    asyncio.run(account.execution.advance_pause(account.portfolio))
                elif kind == "resume":
                    account.execution.set_pause(account.portfolio, "RUNNING", "owner", "synthetic resume")
                    asyncio.run(account.execution.advance_pause(account.portfolio))
                elif kind == "later":
                    account.clock.advance(item[1])
                elif kind == "replan":
                    account.clock.advance(1)
                    account.quote("SOL/USD", "99.9", "100.1")
                    try:
                        asyncio.run(plans.install(account.portfolio, plan(entries=[entry("2", "99", "97", "103")]),
                                                  request_id=f"plan-{number}"))
                    except TradeGraphError:
                        pass  # refused (paused, an unsettled entry): visible, and nothing else changes
                else:
                    account.database.close()
                    account = _restart(account)
                    faulty = _Faulty(account.broker, refuse_stop=refuse_stop)
                    account.execution.broker = faulty
                    plans, actions = DayPlans(account.execution), DayPlanActions(account.execution)
                asyncio.run(plans.manage(account.portfolio))
                _check(account, settled=True)
        finally:
            account.database.close()
