"""Reconciliation settles a lost acknowledgement the broker can see (W8-B, C4; W4-A's finding, W5-A's proposed fix).

An ``UNKNOWN`` paper intent whose order the broker reports ``open`` or ``partially_filled``, with every fill it
reports already recorded, becomes ``OPEN`` or ``PARTIALLY_FILLED`` with its venue order id: unknown was never
rejection, and once the broker shows the order the coin is no longer blocked as "unresolved". Nothing is sent
again. When the recorded fills differ from the broker's figure the intent stays ``UNKNOWN``. A live binding keeps
today's behaviour (unknown live effects stay under owner review; the live question is the owner's). Synthetic
broker history; no network, key or real order.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.integration.test_execution_reconciliation_ordering import _fill, _intent, _stack

from trade_graph.adapters.brokers.paper import DropAckBroker
from trade_graph.contracts.models import OrderLookupResult

D = Decimal


def _status(broker, intent, status: str, filled: str = "0", venue_order_id: str | None = "venue-1") -> None:
    broker.statuses[intent.client_order_id] = OrderLookupResult(status=status, filled_quantity=filled,
                                                                venue_order_id=venue_order_id)


def test_an_unknown_paper_order_the_broker_shows_open_becomes_open_with_its_venue_order_id(tmp_path) -> None:
    database, _clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, _clock, portfolio, "lost-ack", status="UNKNOWN")
    _status(broker, intent, "open")
    asyncio.run(execution.reconcile())
    assert execution.intent_state("lost-ack") == "OPEN"
    assert execution._payload("lost-ack")["venue_order_id"] == "venue-1"
    assert not any(kind == "submit" for kind, _ in broker.calls)  # nothing is sent again
    assert not execution._uncertain_exposure("BTC/USD")  # the coin is no longer blocked as unresolved


def test_a_crash_while_submitting_is_settled_by_the_same_reconciliation(tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "crashed", status="SUBMITTING")
    _status(broker, intent, "open")
    asyncio.run(execution.reconcile())
    assert execution.intent_state("crashed") == "OPEN"
    assert execution._payload("crashed")["venue_order_id"] == "venue-1"


def test_an_unknown_order_partly_filled_at_the_broker_becomes_partially_filled_once_its_fills_are_recorded(
        tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "half", quantity="2", status="UNKNOWN")
    broker.fills = [_fill(clock, intent, "trade-1", quantity="1")]
    _status(broker, intent, "partially_filled", "1", "venue-half")
    asyncio.run(execution.reconcile())
    assert execution.intent_state("half") == "PARTIALLY_FILLED"
    assert execution._payload("half")["venue_order_id"] == "venue-half"
    assert execution._filled_quantity("half") == D("1")


def test_an_unknown_order_stays_unknown_while_the_recorded_fills_differ_from_the_brokers(tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "missing", quantity="2", status="UNKNOWN")
    _status(broker, intent, "partially_filled", "1")  # the broker reports a fill its history does not show yet
    asyncio.run(execution.reconcile())
    assert execution.intent_state("missing") == "UNKNOWN"
    assert "venue_order_id" not in execution._payload("missing")
    assert execution._uncertain_exposure("BTC/USD")


def test_an_order_the_broker_cannot_answer_for_stays_unknown(tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    _intent(database, clock, portfolio, "silent", status="UNKNOWN")  # the broker answers "unknown"
    asyncio.run(execution.reconcile())
    assert execution.intent_state("silent") == "UNKNOWN"


def test_a_cancel_still_unanswered_is_not_reopened(tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "cancelling", status="CANCEL_PENDING")
    _status(broker, intent, "open")
    asyncio.run(execution.reconcile())
    assert execution.intent_state("cancelling") == "CANCEL_PENDING"


def test_a_live_binding_keeps_an_unknown_order_unknown(tmp_path) -> None:
    database, clock, _ledger, portfolio, broker, execution = _stack(
        tmp_path, account="synthetic-live-account", venue="kraken", mode="live")
    intent = _intent(database, clock, portfolio, "live-lost", status="UNKNOWN", account="synthetic-live-account",
                     venue="kraken", mode="live")
    _status(broker, intent, "open")
    asyncio.run(execution.reconcile())
    assert execution.intent_state("live-lost") == "UNKNOWN"  # live behaviour does not change (owner question)


def test_a_lost_acknowledgement_on_the_paper_broker_reopens_the_coin_for_new_entries(tmp_path) -> None:
    from tests.day_plan_support import day_plan_account

    account = day_plan_account(tmp_path)
    inner = account.broker
    account.execution.broker = DropAckBroker(inner)
    first = account.authorize("enter", "SOL/USD", "1", limit="99")
    account.dispatch()
    assert account.state(first) == "UNKNOWN" and inner.submit_count == 1
    asyncio.run(account.execution.reconcile())
    assert account.state(first) == "OPEN"
    assert account.execution._payload(first)["venue_order_id"].startswith("paper-")
    account.execution.broker = inner
    second = account.authorize("enter", "SOL/USD", "1", limit="98")  # no longer "unresolved execution"
    account.dispatch()
    assert account.state(second) == "OPEN" and inner.submit_count == 2
