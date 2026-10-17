"""Day plans across crashes and restarts (T34): no entry rests twice, unknown status is reconciled before anything is
replaced, and fills learned at restart get their protective exits. Synthetic account and quotes; a "crash" is a
process that stops between two steps, and a restart opens new objects on the same database file.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from tests.day_plan_support import broker_orders, day_plan_account, entry, install, manage, plan, protected_quantity
from tests.money_limit_support import Account

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.day_plans import DayPlans
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Observation

D = Decimal


def _restart(account: Account, broker_factory=PaperBroker) -> Account:
    database = Database(account.database.path)
    ledger = Ledger(database, account.clock)
    broker = broker_factory(database, account.clock)
    execution = Execution(database, ledger, account.clock, broker)
    return Account(account.clock, database, ledger, broker, execution, account.portfolio, account._sequence)


def _startup(account: Account) -> None:
    asyncio.run(account.execution.startup())


def _buys_at_broker(account: Account) -> int:
    return len(broker_orders(account, side="buy"))


class _DiesAfterSending:
    """The broker accepts the order, then the process dies before the reply is recorded."""

    def __init__(self, inner: PaperBroker) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def submit(self, intent):
        await self.inner.submit(intent)
        raise RuntimeError("synthetic: the process stopped here")


def test_a_crash_after_installation_sends_each_entry_exactly_once_after_restart(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97"), entry("3", "98", "96")]))
    assert _buys_at_broker(account) == 0  # persisted, not sent: then the process stops
    restarted = _restart(account)
    _startup(restarted)
    assert _buys_at_broker(restarted) == 2 and restarted.broker.submit_count == 2
    again = install(restarted, plan(entries=[entry("5", "99.5", "97"), entry("3", "98", "96")]))
    assert again.plan_id == installed.plan_id and not again.created
    _startup(restarted)
    restarted.dispatch()
    assert _buys_at_broker(restarted) == 2 and restarted.broker.submit_count == 2


def test_a_crash_while_sending_is_reconciled_and_never_resent(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    inner = account.broker
    account.execution.broker = _DiesAfterSending(inner)
    with pytest.raises(RuntimeError, match="stopped here"):
        account.dispatch()
    assert account.state(installed.entries[0].intent_id) == "SUBMITTING"
    restarted = _restart(account)
    _startup(restarted)
    assert restarted.state(installed.entries[0].intent_id) == "UNKNOWN"  # unknown is not rejection
    restarted.dispatch()
    assert _buys_at_broker(restarted) == 1 and inner.submit_count == 1 and restarted.broker.submit_count == 0
    # The order really is resting: it fills, and its fill is protected like any other.
    restarted.clock.advance(1)
    restarted.quote("SOL/USD", "99.4", "99.5", volume="100")
    assert restarted.state(installed.entries[0].intent_id) == "FILLED"
    manage(restarted)
    assert protected_quantity(restarted) == D("5") == restarted.held("SOL")


def test_fills_the_broker_made_while_the_service_was_down_get_their_exits_at_restart(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    account.clock.advance(1)
    quote = Observation(observation_id="while-down", venue="paper", symbol="SOL/USD",
                        event_time_utc=account.clock.now(), available_at_utc=account.clock.now(), bid="99.4",
                        ask="99.5", bid_size="1000", ask_size="1000", volume="3", kind="quote", source="synthetic")
    account.broker.match(quote)  # the broker filled the bid; the process stopped before recording it
    assert account.held("SOL") == D("0")
    restarted = _restart(account)
    _startup(restarted)
    assert restarted.held("SOL") == D("3")
    assert protected_quantity(restarted) == D("3")  # the startup dispatch sent the exit recorded with the fill
    assert manage(restarted)["protected"] == []


def test_an_exit_persisted_with_its_fill_is_sent_after_a_crash(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.4", "99.5", volume="100")  # the fill and its exit intent are recorded together
    assert protected_quantity(account) == D("0")  # then the process stops before sending the exit
    restarted = _restart(account)
    _startup(restarted)
    assert protected_quantity(restarted) == D("5") == restarted.held("SOL")


def test_a_crash_in_the_middle_of_a_replacement_leaves_no_duplicate_and_the_retry_installs(tmp_path,
                                                                                           monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    account.dispatch()
    plans = DayPlans(account.execution)

    def stopped(*args, **kwargs):
        raise RuntimeError("synthetic: the process stopped after cancelling")

    monkeypatch.setattr(plans, "_install", stopped)
    revised = plan(entries=[entry("4", "98.5", "96")])
    with pytest.raises(RuntimeError):
        asyncio.run(plans.install(account.portfolio, revised, request_id="plan-2"))
    assert account.state(first.entries[0].intent_id) == "CANCELLED"
    restarted = _restart(account)
    _startup(restarted)
    second = install(restarted, revised, request_id="plan-2")
    assert second.supersedes_plan_id == first.plan_id and len(second.entries) == 1
    assert install(restarted, revised, request_id="plan-2").plan_id == second.plan_id
    restarted.dispatch()
    assert [order["limit_price"] for order in broker_orders(restarted, side="buy", live=True)] == ["98.5"]


def test_management_after_restarts_never_protects_a_fill_twice(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.4", "99.5", volume="2")
    current = account
    for _ in range(3):
        manage(current)
        current = _restart(current)
        _startup(current)
    manage(current)
    exits = DayPlans(current.execution).status(installed.plan_id)["entries"][0]["exits"]
    assert len(exits) == 1 and protected_quantity(current) == D("2") == current.held("SOL")
