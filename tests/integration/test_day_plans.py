"""Trader day plans (T34): validation, installation before effects, exits attached on fill, pauses, expiry and
replacement. Synthetic paper account, scripted quotes and synthetic money limits only; no network, key or model.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
from tests.day_plan_support import (
    STRATEGY,
    broker_orders,
    day_plan_account,
    entry,
    install,
    manage,
    plan,
    protected_quantity,
    record_report,
)
from tests.money_limit_support import synthetic_policy

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.day_plans import DayPlans
from trade_graph.application.money_limits import DAY_PLAN_ORDERS_OFF
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, StaleState, ValidationFailure

D = Decimal


def _count(account, table: str) -> int:
    return account.database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _refusals(account) -> list[dict]:
    rows = account.database.execute("SELECT payload_json FROM activity_events WHERE kind = 'day_plan_refused' "
                                    "ORDER BY rowid").fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _cross(account, bid: str, ask: str, **options) -> list[str]:
    account.clock.advance(1)
    return account.quote("SOL/USD", bid, ask, **options)


# --- the gate and deterministic validation --------------------------------------------------------------------


def test_plans_stay_off_until_the_owner_records_money_limits(tmp_path) -> None:
    account = day_plan_account(tmp_path, limits=None)
    with pytest.raises(AuthorityDenied, match="money-limits"):
        install(account)
    assert DAY_PLAN_ORDERS_OFF in _refusals(account)[0]["reason"]
    for table in ("day_plans", "day_plan_entries", "order_intents", "price_alarms"):
        assert _count(account, table) == 0


def test_a_strategy_version_needs_a_practice_range_confirmation_report(tmp_path) -> None:
    account = day_plan_account(tmp_path, report=False)
    with pytest.raises(ValidationFailure, match="not a registered"):
        install(account)
    record_report(account, phase="tune")
    with pytest.raises(ValidationFailure, match="confirmation report"):
        install(account)
    assert _count(account, "order_intents") == 0
    report = record_report(account)
    installed = install(account)
    assert installed.practice_reports == (report,)
    assert installed.strategy_ref == STRATEGY.ref


def test_installation_persists_plan_entries_reservations_and_alarms_before_anything_is_sent(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "98", "96")]))
    assert installed.created and installed.symbol == "SOL/USD" and len(installed.entries) == 2
    assert account.broker.submit_count == 0 and broker_orders(account) == []
    for item in installed.entries:
        assert account.state(item.intent_id) == "SUBMISSION_PENDING"
        assert account.database.execute("SELECT COUNT(*) FROM outbox WHERE payload_ref = ? AND status = 'pending'",
                                        (item.intent_id,)).fetchone()[0] == 1
        assert account.database.execute("SELECT COUNT(*) FROM position_reservations WHERE intent_id = ? AND "
                                        "state = 'held'", (item.intent_id,)).fetchone()[0] == 1
    alarms = AlarmBook(account.database, account.clock).alarms(department="trader")
    assert [alarm.day_plan_ref for alarm in alarms] == [installed.plan_id]
    assert installed.alarm_ids == (alarms[0].alarm_id,)
    assert account.dispatch() == 2
    resting = broker_orders(account, side="buy", live=True)
    assert sorted((order["order_type"], order["limit_price"], order["quantity"]) for order in resting) == [
        ("limit", "98", "3"), ("limit", "99.5", "5")]


def test_a_retried_installation_returns_the_same_plan_and_rests_nothing_twice(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    again = install(account)
    assert (again.plan_id, again.created) == (first.plan_id, False)
    assert [item.intent_id for item in again.entries] == [item.intent_id for item in first.entries]
    assert _count(account, "order_intents") == 1 and _count(account, "price_alarms") == 1
    with pytest.raises(DuplicateRecord):
        install(account, plan(entries=[entry("4", "99.5", "97")]))


@pytest.mark.parametrize("changes, error, message", [
    ({"entries": [entry("5", "99.505", "97")]}, ValidationFailure, "price increment"),
    ({"entries": [entry("5", "99.5", "97.001")]}, ValidationFailure, "price increment"),
    ({"entries": [entry("5", "99.5", "97", "104.001")]}, ValidationFailure, "price increment"),
    ({"entries": [entry("5.00001", "99.5", "97")]}, ValidationFailure, "quantity increment"),
    ({"entries": [entry("0.001", "99.5", "97")]}, ValidationFailure, "minimum"),
    ({"entries": [entry("5", "100.1", "97")]}, ValidationFailure, "rests below the market"),
    ({"entries": [entry("5", "101", "97")]}, ValidationFailure, "rests below the market"),
    ({"entries": [entry("5", "99.5", "97", expires_at_utc="2026-10-17T00:00:01Z")]}, ValidationFailure,
     "trading day"),
    ({"entries": [entry("5", "99.5", "97", expires_at_utc="2026-10-16T07:00:00Z")]}, ValidationFailure,
     "future"),
    ({"symbol": "ADA/USD", "alarms": [], "entries": [entry("100", "0.4", "0.3")]}, AuthorityDenied, "outside"),
    ({"symbol": "BTCPERP/USD", "alarms": [], "entries": [entry("0.01", "40000", "39000")]}, AuthorityDenied,
     "D127"),
])
def test_entries_are_checked_exactly_and_a_refused_plan_writes_nothing(tmp_path, changes, error, message) -> None:
    account = day_plan_account(tmp_path)
    with pytest.raises(error, match=message):
        install(account, plan(**changes))
    for table in ("day_plans", "day_plan_entries", "order_intents", "price_alarms", "position_reservations"):
        assert _count(account, table) == 0
    assert _refusals(account)[-1]["request_id"] == "plan-1"


def test_the_whole_plan_fits_the_money_limits_counting_resting_entries_as_filled(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    # 30% of USD10,000 per coin: 20 SOL at 99.5 fit, a second 15 at 98 would not (counted at the market, D181).
    with pytest.raises(AuthorityDenied, match="exposure"):
        install(account, plan(entries=[entry("20", "99.5", "97"), entry("15", "98", "96")]))
    assert _count(account, "order_intents") == 0 and _count(account, "day_plans") == 0
    installed = install(account, plan(entries=[entry("20", "99.5", "97"), entry("8", "98", "96")]))
    assert len(installed.entries) == 2


def test_no_confidence_figure_committee_or_leader_approval_is_involved(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    assert installed.created
    for table in ("leader_decisions",):
        exists = account.database.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        if exists:
            assert _count(account, table) == 0
    document = json.loads(account.database.execute("SELECT document_json FROM day_plans").fetchone()[0])
    assert not {"confidence", "approval", "approved_by", "committee"} & set(document)


def test_day_plans_install_on_the_paper_account_only(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    account.execution.venue = "kraken"
    with pytest.raises(AuthorityDenied, match="paper"):
        install(account)
    assert _count(account, "day_plans") == 0


def test_a_pause_refuses_new_plans(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    account.execution.set_pause(account.portfolio, "MANAGE_ONLY", "system", "synthetic")
    with pytest.raises(AuthorityDenied, match="MANAGE_ONLY"):
        install(account)


# --- protective exits attached on fill ------------------------------------------------------------------------


def test_a_filled_entry_carries_its_protective_exit_at_once(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")  # the ask reached the resting bid
    entry_intent = installed.entries[0].intent_id
    assert account.state(entry_intent) == "FILLED" and account.held("SOL") == D("5")
    plans = DayPlans(account.execution)
    exits = plans.status(installed.plan_id)["entries"][0]["exits"]
    assert [(item["quantity"], item["stop_price"], item["take_profit_price"], item["cause"]) for item in exits] == [
        ("5", "97", "104", "fill")]
    assert account.state(exits[0]["intent_id"]) == "SUBMISSION_PENDING"  # persisted with the fill, then sent
    manage(account, plans)
    assert protected_quantity(account) == D("5")
    stop = broker_orders(account, side="sell", live=True)[0]
    assert (stop["stop_price"], stop["take_profit_price"]) == ("97", "104")


def test_every_partial_fill_is_protected_and_the_stop_closes_the_whole_position(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="2.0004")
    manage(account)
    _cross(account, "99.4", "99.5", volume="1.5")
    manage(account)
    assert account.held("SOL") == D("3.5004") and protected_quantity(account) == D("3.5004")
    assert account.state(installed.entries[0].intent_id) == "PARTIALLY_FILLED"
    # The price falls through the stop: both exits sell, and the falling ask also fills the rest of the resting
    # bid, whose part gets its own exit at once.
    _cross(account, "96.9", "97.1", volume="100")
    assert account.held("SOL") == D("1.4996")
    assert account.state(installed.entries[0].intent_id) == "FILLED"
    manage(account)
    assert protected_quantity(account) == D("1.4996")
    _cross(account, "96.8", "97")
    assert account.held("SOL") == D("0") and protected_quantity(account) == D("0")
    status = DayPlans(account.execution).status(installed.plan_id)["entries"][0]
    assert [(item["quantity"], item["state"]) for item in status["exits"]] == [
        ("2.0004", "FILLED"), ("1.5", "FILLED"), ("1.4996", "FILLED")]
    assert status["unprotected"] == "0"


def test_the_take_profit_closes_the_position_and_cancels_the_stop(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")
    manage(account)
    _cross(account, "104.1", "104.3")  # the bid traded above the take-profit
    assert account.held("SOL") == D("0") and protected_quantity(account) == D("0")
    sells = [order for order in broker_orders(account, side="sell")]
    assert [order["status"] for order in sells] == ["filled"]
    fill = json.loads(account.database.execute(
        "SELECT document_json FROM fills WHERE intent_id = (SELECT intent_id FROM day_plan_exits)").fetchone()[0])
    assert D(fill["price"]) == D("103.89")  # the target less the 0.10% exit slippage, on the 0.01 tick


def test_a_fill_whose_exit_cannot_be_placed_yet_is_protected_by_the_next_management_step(tmp_path,
                                                                                          monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    account.dispatch()
    original = account.execution.place_protection

    def refused(*args, **kwargs):
        raise StaleState("synthetic: protection unavailable")

    monkeypatch.setattr(account.execution, "place_protection", refused)
    _cross(account, "99.4", "99.5", volume="100")
    assert account.held("SOL") == D("5") and protected_quantity(account) == D("0")  # the fill is still recorded
    events = DayPlans(account.execution).status(installed.plan_id)["events"]
    assert [event["kind"] for event in events][-1] == "protection_deferred"
    monkeypatch.setattr(account.execution, "place_protection", original)
    result = manage(account)
    assert len(result["protected"]) == 1 and protected_quantity(account) == D("5")
    assert manage(account)["protected"] == []  # nothing is protected twice


# --- pauses, the loss stop and expiry -------------------------------------------------------------------------


@pytest.mark.parametrize("profile, originator", [("MANAGE_ONLY", "system"), ("NO_NEW_EXPOSURE", "owner"),
                                                  ("PAUSE_DECISIONS", "leader")])
def test_a_pause_cancels_resting_entries_and_keeps_protective_exits(tmp_path, profile, originator) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "98", "96")]))
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="2")
    manage(account)
    account.execution.set_pause(account.portfolio, profile, originator, "synthetic pause")
    result = manage(account)
    asyncio.run(account.execution.advance_pause(account.portfolio))
    states = [account.state(item.intent_id) for item in installed.entries]
    assert states == ["CANCELLED", "CANCELLED"]
    assert sorted(result["cancelled"]) == sorted(item.intent_id for item in installed.entries)
    assert broker_orders(account, side="buy", live=True) == []
    assert account.held("SOL") == D("2") and protected_quantity(account) == D("2")
    status = DayPlans(account.execution).status(installed.plan_id)
    assert {event["reason"] for event in status["events"] if event["kind"] == "entry_cancelled"} <= {"pause"}
    # The stop still works under the pause.
    _cross(account, "96.5", "96.7")
    assert account.held("SOL") == D("0")


def test_a_fill_just_before_a_pause_still_gets_its_exit_sent(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")  # the exit intent is persisted with the fill
    account.execution.set_pause(account.portfolio, "PAUSE_DECISIONS", "leader", "synthetic pause")
    manage(account)
    assert protected_quantity(account) == D("5")


def test_cancel_all_removes_exits_by_the_owners_choice_and_running_again_restores_them(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")
    manage(account)
    account.execution.set_pause(account.portfolio, "CANCEL_ALL", "owner", "synthetic cancel all")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    manage(account)
    assert protected_quantity(account) == D("0") and account.held("SOL") == D("5")
    account.execution.set_pause(account.portfolio, "RUNNING", "owner", "resume")
    result = manage(account)
    assert len(result["protected"]) == 1 and protected_quantity(account) == D("5")


def test_the_daily_loss_stop_cancels_resting_entries_and_keeps_exits(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("25", "99.5", "60"), entry("4", "70", "60")]))
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="25")
    manage(account)
    assert account.held("SOL") == D("25") and protected_quantity(account) == D("25")
    _cross(account, "82", "82.2")  # a 17% fall on 25 SOL loses more than 4% of the day-start value
    account.enforce()
    manage(account)
    assert account.state(installed.entries[1].intent_id) == "CANCELLED"
    assert protected_quantity(account) == D("25")
    with pytest.raises(AuthorityDenied, match="loss limit"):
        install(account, plan(entries=[entry("1", "81", "60")]), request_id="plan-2")


def test_an_entry_still_resting_at_its_expiry_is_cancelled_and_a_filled_part_keeps_its_exit(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", expires_at_utc="2026-10-16T09:00:00Z")]))
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="2")
    manage(account)
    account.clock.advance(3600)
    account.quote("SOL/USD", "99.9", "100.1")
    result = manage(account)
    assert result["cancelled"] == [installed.entries[0].intent_id]
    assert account.state(installed.entries[0].intent_id) == "CANCELLED"
    reasons = [event["reason"] for event in DayPlans(account.execution).status(installed.plan_id)["events"]
               if event["kind"] == "entry_cancelled"]
    assert reasons == ["expired"]
    assert protected_quantity(account) == D("2")


def test_entries_expire_at_the_end_of_the_trading_day_by_default(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    assert installed.entries[0].expires_at == "2026-10-17T00:00:00.000000Z"
    assert installed.trading_day_end == "2026-10-17T00:00:00.000000Z"


# --- replacement and unknown status ---------------------------------------------------------------------------


def test_a_new_plan_for_the_coin_replaces_the_resting_entries_and_alarms_of_the_last(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="2")
    manage(account)
    second = install(account, plan(entries=[entry("4", "98.5", "96")], alarms=[]), request_id="plan-2")
    assert second.supersedes_plan_id == first.plan_id
    assert account.state(first.entries[0].intent_id) == "CANCELLED"
    assert AlarmBook(account.database, account.clock).get(first.alarm_ids[0]).status == "cancelled"
    assert protected_quantity(account) == D("2")  # the position from the first plan keeps its exit
    account.dispatch()
    assert [order["limit_price"] for order in broker_orders(account, side="buy", live=True)] == ["98.5"]
    plans = DayPlans(account.execution)
    assert [item.plan_id for item in plans.active(account.portfolio)] == [second.plan_id]
    assert {event["kind"] for event in plans.status(first.plan_id)["events"]} >= {"installed", "superseded"}


class _CancelUncertain:
    """Wraps the paper broker: cancellations get no answer, so their outcome stays unknown."""

    def __init__(self, inner) -> None:
        self.inner, self.answer = inner, False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def cancel(self, request):
        if self.answer:
            return await self.inner.cancel(request)
        from trade_graph.contracts.models import CancelResult

        return CancelResult(status="uncertain", error="timeout_uncertain", message="synthetic: no answer")

    async def order_status(self, key):
        result = await self.inner.order_status(key)
        return result


def test_an_entry_whose_status_is_unknown_is_reconciled_before_any_replacement(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    account.dispatch()
    wrapped = _CancelUncertain(account.broker)
    account.execution.broker = wrapped
    with pytest.raises(StaleState, match="reconciled"):
        install(account, plan(entries=[entry("4", "98.5", "96")]), request_id="plan-2")
    assert account.state(first.entries[0].intent_id) == "CANCEL_PENDING"
    assert _count(account, "day_plans") == 1 and _count(account, "order_intents") == 1
    wrapped.answer = True
    second = install(account, plan(entries=[entry("4", "98.5", "96")]), request_id="plan-2")
    assert account.state(first.entries[0].intent_id) == "CANCELLED"
    assert second.supersedes_plan_id == first.plan_id


def test_a_lost_acknowledgement_is_reconciled_and_never_resent(tmp_path) -> None:
    from trade_graph.adapters.brokers.paper import DropAckBroker

    account = day_plan_account(tmp_path)
    inner = account.broker
    account.execution.broker = DropAckBroker(inner)
    first = install(account)
    account.dispatch()
    assert account.state(first.entries[0].intent_id) == "UNKNOWN" and inner.submit_count == 1
    account.dispatch()
    assert inner.submit_count == 1  # unknown is not rejection: nothing is sent again
    second = install(account, plan(entries=[entry("4", "98.5", "96")]), request_id="plan-2")
    assert account.state(first.entries[0].intent_id) == "CANCELLED"
    account.dispatch()
    assert len(broker_orders(account, side="buy")) == 2 and inner.submit_count == 2
    assert second.supersedes_plan_id == first.plan_id


def test_plans_of_different_coins_live_side_by_side(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    sol = install(account)
    btc = install(account, plan(symbol="BTC/USD", alarms=[], entries=[entry("0.01", "49000", "47000")]),
                  request_id="plan-btc")
    plans = DayPlans(account.execution)
    assert {item.plan_id for item in plans.active(account.portfolio)} == {sol.plan_id, btc.plan_id}
    assert btc.supersedes_plan_id is None


def test_a_plan_records_the_policy_revision_it_was_installed_under(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    account.install(synthetic_policy("synthetic-2"))
    installed = install(account)
    assert installed.policy_revision == "synthetic-2"
