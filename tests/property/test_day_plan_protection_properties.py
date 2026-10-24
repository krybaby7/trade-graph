"""Every open paper position from a day plan carries a protective exit while the Trader sleeps (T34 acceptance).

Random sequences of quotes (whole and partial fills, with and without a volume figure, stops and take-profits),
the Trader cancelling an entry, entries expiring, pauses of every protecting kind and the owner's cancel-all, and
restarts (new objects on the same database file, then the startup reconciliation). After every management step
the coins held equal what live protective exits at the broker would sell, whenever the pause keeps protection; the
exits never ask to sell more than is held; no entry ever reaches the broker twice. Right after a fill, before
anything is sent, the persisted exit intents already cover what is held.

Synthetic account (USD10,000; 4% a day, 30% per coin, 90% in total), scripted quotes; no network, model or key.
"""

from __future__ import annotations

import asyncio
import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.day_plan_support import broker_orders, day_plan_account, entry, install, plan, protected_quantity
from tests.money_limit_support import Account

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.day_plan_protection import OUTSTANDING
from trade_graph.application.day_plans import DayPlans
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.kernel.authority import pause_allows_protection

D = Decimal
ASKS = ["101", "100.1", "99.5", "99.2", "98", "97.6", "97", "96", "95.5", "94", "93", "101.5", "102", "104.5"]
VOLUMES = [None, "0", "0.0003", "0.5", "1.2345", "2", "100"]
PAUSES = ["MANAGE_ONLY", "NO_NEW_EXPOSURE", "PAUSE_DECISIONS", "CANCEL_ALL"]

step = st.one_of(
    st.tuples(st.just("quote"), st.sampled_from(ASKS), st.sampled_from(VOLUMES)),
    st.tuples(st.just("pause"), st.sampled_from(PAUSES)),
    st.tuples(st.just("resume")),
    st.tuples(st.just("cancel"), st.integers(0, 2)),
    st.tuples(st.just("later"), st.sampled_from([60, 900])),
    st.tuples(st.just("restart")),
)


def _restart(account: Account) -> Account:
    database = Database(account.database.path)
    ledger = Ledger(database, account.clock)
    broker = PaperBroker(database, account.clock)
    execution = Execution(database, ledger, account.clock, broker)
    restarted = Account(account.clock, database, ledger, broker, execution, account.portfolio, account._sequence)
    asyncio.run(restarted.execution.startup())
    return restarted


def _persisted_cover(account: Account) -> Decimal:
    """What the exit intents still outstanding would sell, from the records alone (sent or not)."""
    rows = account.database.execute("SELECT intent_id, quantity FROM day_plan_exits").fetchall()
    total = D("0")
    for row in rows:
        if account.state(row["intent_id"]) in OUTSTANDING:
            total += D(row["quantity"]) - account.execution._filled_quantity(row["intent_id"])
    return total


def _check(account: Account, *, after_management: bool) -> None:
    held = account.held("SOL")
    reserved = account.execution._reserved(account.portfolio, "SOL")
    assert held >= 0 and reserved <= held  # spot only: nothing is ever sold that is not held
    protects = pause_allows_protection(account.execution.profile(account.portfolio))
    if protects:
        assert _persisted_cover(account) == held
        if after_management:
            assert protected_quantity(account) == held
    else:
        assert protected_quantity(account) <= held
    buys = broker_orders(account, side="buy")
    assert len({order["intent_id"] for order in buys}) == len(buys) <= 3


@settings(max_examples=45, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(steps=st.lists(step, min_size=1, max_size=24))
def test_open_positions_always_carry_their_protective_exits(steps) -> None:
    with tempfile.TemporaryDirectory() as folder:
        account = day_plan_account(Path(folder))
        installed = install(account, plan(entries=[
            entry("5", "99.5", "97", "104"),
            entry("3", "98", "95.5"),
            entry("4", "96", "94", "101", expires_at_utc="2026-10-16T09:30:00Z"),
        ]))
        plans = DayPlans(account.execution)
        intents = [item.intent_id for item in installed.entries]
        account.dispatch()
        try:
            for item in steps:
                kind = item[0]
                if kind == "quote":
                    ask = D(item[1])
                    account.clock.advance(1)
                    account.quote("SOL/USD", str(ask - D("0.1")), str(ask), volume=item[2])
                    _check(account, after_management=False)
                elif kind == "pause":
                    account.execution.set_pause(account.portfolio, item[1], "owner", "synthetic pause")
                    asyncio.run(account.execution.advance_pause(account.portfolio))
                elif kind == "resume":
                    account.execution.set_pause(account.portfolio, "RUNNING", "owner", "synthetic resume")
                    asyncio.run(account.execution.advance_pause(account.portfolio))
                elif kind == "cancel":
                    intent_id = intents[item[1]]
                    if account.state(intent_id) in {"OPEN", "PARTIALLY_FILLED"}:
                        asyncio.run(account.execution.cancel(intent_id))
                elif kind == "later":
                    account.clock.advance(item[1])
                else:
                    account.database.close()
                    account = _restart(account)
                    plans = DayPlans(account.execution)
                asyncio.run(plans.manage(account.portfolio))
                _check(account, after_management=True)
        finally:
            account.database.close()
