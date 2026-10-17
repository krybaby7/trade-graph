"""The Trader's exit tools (W8-B, C4): close a day-plan position or move its safety exit, as one recorded sequence.

Software cancels the coin's exits, reconciles, then sells or attaches the new exit. An open position is never left
without a safety exit except during the sell itself; a refused or unknown step leaves the old exit in place (it is
kept, or put back with its own terms). Nothing here is a confidence threshold, committee or approval: only
deterministic facts are checked. Synthetic paper account, scripted quotes and brokers; no network, key or model.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from tests.day_plan_action_support import act, fill_quote, filled, live_exits, market_sells, position_action, steps
from tests.day_plan_support import broker_orders, day_plan_account, entry, install, manage, plan, protected_quantity

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.day_plan_actions import DayPlanActions
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import CancelResult, SubmitResult
from trade_graph.domain.errors import DuplicateRecord, StaleState, UncertainExternal, ValidationFailure
from trade_graph.kernel.authority import path_is_protected

D = Decimal
TWO_ENTRIES = plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "99", "96")])


class _Broker:
    """Wraps the paper broker; subclasses change one answer. Records the order of cancels and submissions."""

    def __init__(self, inner: PaperBroker) -> None:
        self.inner, self.timeline = inner, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def cancel(self, request):
        self.timeline.append(("cancel", request.client_order_id))
        return await self.inner.cancel(request)

    async def submit(self, intent):
        self.timeline.append(("submit", intent.order_type, intent.client_order_id))
        return await self.inner.submit(intent)


class _CancelUnanswered(_Broker):
    """Cancels of the named orders get no answer (all of them when none is named)."""

    def __init__(self, inner, only: set[str] | None = None) -> None:
        super().__init__(inner)
        self.only = only

    async def cancel(self, request):
        if self.only is None or request.client_order_id in self.only:
            self.timeline.append(("cancel-unanswered", request.client_order_id))
            return CancelResult(status="uncertain", error="timeout_uncertain", message="synthetic: no answer")
        return await super().cancel(request)


class _Refuses(_Broker):
    """Refuses market sells, or stops at one stop price."""

    def __init__(self, inner, *, market: bool = False, stop: str | None = None) -> None:
        super().__init__(inner)
        self.market, self.stop = market, stop

    async def submit(self, intent):
        refused = ((self.market and intent.order_type == "market")
                   or (self.stop is not None and intent.stop_price == D(self.stop)))
        if refused:
            self.timeline.append(("refused", intent.order_type, intent.client_order_id))
            return SubmitResult(status="rejected", error="rejected", message="synthetic venue refusal")
        return await super().submit(intent)


class _LosesAck(_Broker):
    """Accepts the order with this stop and loses the acknowledgement."""

    def __init__(self, inner, stop: str) -> None:
        super().__init__(inner)
        self.stop = stop

    async def submit(self, intent):
        result = await super().submit(intent)
        if intent.stop_price == D(self.stop):
            raise UncertainExternal("synthetic: acknowledgement lost")
        return result


def _exit_intents(account) -> list[str]:
    return [row["intent_id"] for row in account.database.execute(
        "SELECT intent_id FROM day_plan_exits ORDER BY sequence").fetchall()]


def _client(account, intent_id: str) -> str:
    return account.execution._payload(intent_id)["client_order_id"]


def _orders(account, role: str) -> list[dict]:
    return [dict(row) for row in account.database.execute(
        "SELECT * FROM day_plan_action_orders WHERE role = ? ORDER BY sequence", (role,)).fetchall()]


def _restart(account):
    from tests.money_limit_support import Account

    database = Database(account.database.path)
    ledger = Ledger(database, account.clock)
    broker = PaperBroker(database, account.clock)
    execution = Execution(database, ledger, account.clock, broker)
    restarted = Account(account.clock, database, ledger, broker, execution, account.portfolio, account._sequence)
    asyncio.run(restarted.execution.startup())
    return restarted


# --- closing a position -------------------------------------------------------------------------------------------


def test_a_close_cancels_the_exit_reconciles_then_sells_the_position(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    (exit_intent,) = _exit_intents(account)
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and account.held("SOL") == D("5")
    broker = _Broker(account.broker)
    account.execution.broker = broker
    record = act(account, position_action("close"))
    assert (record.status, record.reason) == ("done", None)
    assert steps(record) == ["cancelling", "cancelled", "sell_placed", "sell_sent", "done"]
    assert account.state(exit_intent) == "CANCELLED" and live_exits(account) == []
    (sell,) = market_sells(account)
    assert D(sell["quantity"]) == D("5") and sell["status"] == "open"
    # The exit was cancelled (and reconciled) before the sell reached the broker.
    assert [event[0] for event in broker.timeline] == ["cancel", "submit"]
    assert broker.timeline[0][1] == _client(account, exit_intent) and broker.timeline[1][1] == "market"
    (part,) = _orders(account, "sell")
    assert (part["intent_id"], D(part["quantity"])) == (sell["intent_id"], D("5"))
    fill_quote(account, bid="99.6", ask="99.7", volume=None)  # the market sell fills at the next quote
    assert account.held("SOL") == 0
    assert manage(account)["protected"] == [] and live_exits(account) == []


def test_a_close_cancels_the_coins_resting_day_plan_entries_before_its_exits(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "98", "95.5")]))
    account.dispatch()
    fill_quote(account)  # the 99.5 entry fills; the 98 entry keeps resting
    resting = installed.entries[1].intent_id
    assert account.state(resting) == "OPEN"
    record = act(account, position_action("close"))
    assert record.status == "done"
    assert steps(record) == ["cancelling", "cancelled", "cancelling", "cancelled", "sell_placed", "sell_sent",
                             "done"]
    assert record.steps[0]["document"] == {"entries": [resting], "exits": []}
    assert account.state(resting) == "CANCELLED"
    fill_quote(account, bid="97.9", ask="98", volume=None)  # the cancelled bid would have filled here
    assert account.held("SOL") == 0 and account.state(resting) == "CANCELLED"


def test_a_close_sells_exits_that_were_never_sent_by_abandoning_them_first(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "99.4", "99.5", volume="100")  # filled; the exit is persisted but not sent yet
    (exit_intent,) = _exit_intents(account)
    assert account.state(exit_intent) == "SUBMISSION_PENDING"
    record = act(account, position_action("close"))
    assert record.status == "done" and account.state(exit_intent) == "REJECTED"
    assert live_exits(account) == [] and len(market_sells(account)) == 1


# --- moving the safety exit ----------------------------------------------------------------------------------------


def test_a_moved_exit_replaces_the_old_one_at_the_new_terms_for_the_same_coins(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    (old,) = _exit_intents(account)
    broker = _Broker(account.broker)
    account.execution.broker = broker
    record = act(account, position_action("move_exit", stop_price="99", take_profit_price="106"))
    assert record.status == "done"
    assert steps(record) == ["cancelling", "cancelled", "exits_attached", "done"]
    assert account.state(old) == "CANCELLED"
    assert live_exits(account) == [(D("99"), D("106"), D("5"))] and protected_quantity(account) == account.held("SOL")
    assert [event[0] for event in broker.timeline] == ["cancel", "submit"]
    (moved,) = _orders(account, "moved_exit")
    assert moved["intent_id"] == _exit_intents(account)[-1] and D(moved["quantity"]) == D("5")
    assert manage(account)["protected"] == []  # nothing is attached twice
    fill_quote(account, bid="98.9", ask="99", volume=None)  # the price falls through the new stop
    assert account.held("SOL") == 0 and live_exits(account) == []


def test_a_moved_exit_may_drop_its_take_profit(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    record = act(account, position_action("move_exit", stop_price="98", take_profit_price=None))
    assert record.status == "done" and live_exits(account) == [(D("98"), None, D("5"))]


def test_the_sweep_re_protects_a_moved_position_at_its_moved_terms_after_the_owners_cancel_all(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    act(account, position_action("move_exit", stop_price="99", take_profit_price="106"))
    account.execution.set_pause(account.portfolio, "CANCEL_ALL", "owner", "synthetic cancel-all")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    assert live_exits(account) == []
    account.execution.set_pause(account.portfolio, "RUNNING", "owner", "synthetic resume")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    manage(account)
    assert live_exits(account) == [(D("99"), D("106"), D("5"))]


def test_partial_fills_move_and_close_only_what_is_held_and_later_fills_keep_their_own_terms(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    account.dispatch()
    fill_quote(account, volume=None)  # half of the 5 fills
    entry_intent = installed.entries[0].intent_id
    assert account.state(entry_intent) == "PARTIALLY_FILLED" and account.held("SOL") == D("2.5")
    moved = act(account, position_action("move_exit", stop_price="99", take_profit_price="106"))
    assert moved.status == "done" and account.state(entry_intent) == "PARTIALLY_FILLED"  # the bid keeps resting
    assert live_exits(account) == [(D("99"), D("106"), D("2.5"))]
    fill_quote(account, volume=None)  # half of the rest fills: its exit is the entry's own (bracket) terms
    assert live_exits(account) == [(D("99"), D("106"), D("2.5")), (D("97"), D("104"), D("1.25"))]
    closed = act(account, position_action("close"), request_id="action-2")
    assert closed.status == "done" and account.state(entry_intent) == "CANCELLED"
    (sell,) = market_sells(account)
    assert D(sell["quantity"]) == D("3.75") and live_exits(account) == []


def test_two_entries_at_different_terms_move_together_and_close_together(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account, TWO_ENTRIES, bid="98.9", ask="99")
    assert sorted(live_exits(account)) == [(D("96"), None, D("3")), (D("97"), D("104"), D("5"))]
    act(account, position_action("move_exit", stop_price="98", take_profit_price="103"))
    assert sorted(live_exits(account)) == [(D("98"), D("103"), D("3")), (D("98"), D("103"), D("5"))]
    act(account, position_action("close"), request_id="action-2")
    (sell,) = market_sells(account)
    assert D(sell["quantity"]) == D("8") and len(_orders(account, "sell")) == 2


# --- refusals and unknown steps leave the old exit in place --------------------------------------------------------


@pytest.mark.parametrize(("fields", "words"), [
    ({"stop_price": "99.4"}, "below the bid"),
    ({"stop_price": "98", "take_profit_price": "99.4"}, "above the bid"),
    ({"stop_price": "98.005"}, "price increment"),
    ({"stop_price": "98", "take_profit_price": "104.001"}, "price increment"),
])
def test_a_move_refused_by_its_terms_leaves_the_old_exit_untouched(tmp_path, fields, words) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    (old,) = _exit_intents(account)
    record = act(account, position_action("move_exit", **fields))
    assert record.status == "refused" and words in record.reason and record.error_code == "validation"
    assert steps(record) == ["refused"]
    assert account.state(old) == "OPEN" and live_exits(account) == [(D("97"), D("104"), D("5"))]


def test_refusals_before_anything_is_cancelled(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    no_position = act(account, position_action("close", symbol="BTC/USD"), request_id="btc")
    assert no_position.status == "refused" and "no open day-plan position" in no_position.reason
    riba = act(account, position_action("close", symbol="BTCPERP/USD"), request_id="perp")
    assert riba.status == "refused" and riba.error_code == "authority_denied"
    account.execution.set_pause(account.portfolio, "PAUSE_DECISIONS", "owner", "synthetic pause")
    paused = act(account, position_action("close"), request_id="paused")
    assert paused.status == "refused" and "PAUSE_DECISIONS" in paused.reason
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and market_sells(account) == []
    assert all(steps(record) == ["refused"] for record in (no_position, riba, paused))


def test_a_cancel_that_gets_no_answer_keeps_the_old_exit_and_nothing_is_sold(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    (old,) = _exit_intents(account)
    account.execution.broker = _CancelUnanswered(account.broker)
    record = act(account, position_action("close"))
    assert record.status == "failed" and "no answer" in record.reason
    assert steps(record) == ["cancelling", "cancelled", "unsettled", "failed"]
    assert account.state(old) == "CANCEL_PENDING"  # unknown is not cancelled: it still covers the coins
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and market_sells(account) == []
    assert manage(account)["protected"] == []  # no second exit for the same coins


def test_when_one_of_two_cancels_gets_no_answer_the_other_exit_is_put_back_at_its_own_terms(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account, TWO_ENTRIES, bid="98.9", ask="99")
    first, second = _exit_intents(account)
    account.execution.broker = _CancelUnanswered(account.broker, only={_client(account, first)})
    record = act(account, position_action("move_exit", stop_price="98", take_profit_price="103"))
    assert record.status == "failed"
    assert steps(record) == ["cancelling", "cancelled", "unsettled", "exits_restored", "failed"]
    assert account.state(first) == "CANCEL_PENDING" and account.state(second) == "CANCELLED"
    assert sorted(live_exits(account)) == [(D("96"), None, D("3")), (D("97"), D("104"), D("5"))]
    (restored,) = _orders(account, "restored_exit")
    cancelled = account.database.execute("SELECT quantity FROM day_plan_exits WHERE intent_id = ?",
                                         (second,)).fetchone()["quantity"]
    assert D(restored["quantity"]) == D(cancelled) and _orders(account, "moved_exit") == []
    assert protected_quantity(account) == account.held("SOL") == D("8")


def test_a_sell_refused_after_the_cancel_puts_the_old_exit_back(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    authorize = account.execution.authorize

    def refuse_exits(portfolio_id, decision, **options):
        if decision.action == "exit":
            raise ValidationFailure("synthetic: the sell is refused")
        return authorize(portfolio_id, decision, **options)

    monkeypatch.setattr(account.execution, "authorize", refuse_exits)
    record = act(account, position_action("close"))
    assert record.status == "refused" and "the sell is refused" in record.reason
    assert steps(record) == ["cancelling", "cancelled", "exits_restored", "refused"]
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and market_sells(account) == []
    assert protected_quantity(account) == account.held("SOL")


def test_a_broker_that_rejects_the_sell_gets_the_old_exit_back(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    account.execution.broker = _Refuses(account.broker, market=True)
    record = act(account, position_action("close"))
    assert record.status == "refused" and "synthetic venue refusal" in record.reason
    assert steps(record) == ["cancelling", "cancelled", "sell_placed", "sell_sent", "exits_restored", "refused"]
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and account.held("SOL") == D("5")


def test_a_broker_that_rejects_the_new_exit_gets_the_old_one_back_and_keeps_its_terms(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    account.execution.broker = _Refuses(account.broker, stop="99")
    record = act(account, position_action("move_exit", stop_price="99", take_profit_price="106"))
    assert record.status == "refused" and "synthetic venue refusal" in record.reason
    assert steps(record) == ["cancelling", "cancelled", "exits_attached", "exits_restored", "refused"]
    assert live_exits(account) == [(D("97"), D("104"), D("5"))]
    # A refused move never becomes the terms the sweep uses later.
    account.execution.broker = account.broker
    account.execution.set_pause(account.portfolio, "CANCEL_ALL", "owner", "synthetic cancel-all")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    account.execution.set_pause(account.portfolio, "RUNNING", "owner", "synthetic resume")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    manage(account)
    assert live_exits(account) == [(D("97"), D("104"), D("5"))]


def test_a_new_exit_whose_acknowledgement_is_lost_still_protects_and_is_never_doubled(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    account.execution.broker = _LosesAck(account.broker, "99")
    record = act(account, position_action("move_exit", stop_price="99", take_profit_price="106"))
    assert record.status == "done"
    moved = _exit_intents(account)[-1]
    assert account.state(moved) == "UNKNOWN"  # unknown is not rejection: it covers the coins
    assert manage(account)["protected"] == []
    asyncio.run(account.execution.reconcile())
    assert account.state(moved) == "OPEN"  # the broker shows it resting (D248)
    assert live_exits(account) == [(D("99"), D("106"), D("5"))]


# --- requests, restarts and abandoned actions ------------------------------------------------------------------


def test_a_retried_request_returns_the_same_action_and_never_acts_twice(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    first = act(account, position_action("close"))
    again = act(account, position_action("close"))
    assert (again.action_id, again.created, first.created) == (first.action_id, False, True)
    assert len(market_sells(account)) == 1
    with pytest.raises(DuplicateRecord):
        act(account, position_action("close", reason="Synthetic: a different reason."))


def test_a_crash_after_the_cancel_is_covered_by_the_sweep_and_the_retry_finishes_the_close(tmp_path,
                                                                                           monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)

    def stop_here(*_args, **_options):
        raise RuntimeError("synthetic: the process stopped here")

    monkeypatch.setattr(account.execution, "authorize", stop_here)
    with pytest.raises(RuntimeError, match="stopped here"):
        act(account, position_action("close"))
    assert live_exits(account) == []  # the exit was cancelled; then the process stopped
    account.database.close()
    restarted = _restart(account)
    manage(restarted)  # the management step's sweep protects the position again (at its terms in force)
    assert live_exits(restarted) == [(D("97"), D("104"), D("5"))]
    record = act(restarted, position_action("close"))  # the engine applies the saved answer again
    assert record.status == "done" and not record.created
    assert steps(record).count("cancelling") == 2 and len(market_sells(restarted)) == 1
    fill_quote(restarted, bid="99.6", ask="99.7", volume=None)
    assert restarted.held("SOL") == 0 and live_exits(restarted) == []


def test_a_crash_after_the_sell_was_placed_never_sells_twice(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    dispatch = account.execution.dispatch

    async def stop_on_targeted_send(**options):
        if options.get("intent_ids"):
            raise RuntimeError("synthetic: the process stopped here")
        return await dispatch(**options)

    monkeypatch.setattr(account.execution, "dispatch", stop_on_targeted_send)
    with pytest.raises(RuntimeError, match="stopped here"):
        act(account, position_action("close"))
    account.database.close()
    restarted = _restart(account)  # the startup dispatch sends the persisted sell
    record = act(restarted, position_action("close"))
    assert record.status == "done" and steps(record)[-3:] == ["sell_placed", "sell_sent", "done"]
    assert len(market_sells(restarted)) == 1 and restarted.broker.submit_count == 1


def test_a_newer_action_for_the_coin_supersedes_an_unfinished_one(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    actions = DayPlanActions(account.execution)
    with monkeypatch.context() as patch:
        patch.setattr(account.execution, "authorize",
                      lambda *_a, **_o: (_ for _ in ()).throw(RuntimeError("synthetic: stopped")))
        with pytest.raises(RuntimeError):
            act(account, position_action("close"), actions=actions)
    earlier = actions.for_request(account.portfolio, "action-1")
    assert earlier.status == "running"
    record = act(account, position_action("move_exit", stop_price="98", take_profit_price=None),
                 request_id="action-2", actions=actions)
    assert record.status == "done"
    earlier = actions.get(earlier.action_id)
    assert earlier.status == "failed" and "superseded" in earlier.reason
    assert live_exits(account) == [(D("98"), None, D("5"))] and market_sells(account) == []


def test_abandoning_an_unfinished_action_puts_the_old_exit_back(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    actions = DayPlanActions(account.execution)
    with monkeypatch.context() as patch:
        patch.setattr(account.execution, "authorize",
                      lambda *_a, **_o: (_ for _ in ()).throw(RuntimeError("synthetic: stopped")))
        with pytest.raises(RuntimeError):
            act(account, position_action("close"), actions=actions)
    assert live_exits(account) == []
    record = asyncio.run(actions.abandon(account.portfolio, "action-1",
                                         reason="the trading day ended before it finished"))
    assert record.status == "failed" and steps(record)[-2:] == ["exits_restored", "failed"]
    assert live_exits(account) == [(D("97"), D("104"), D("5"))]
    assert asyncio.run(actions.abandon(account.portfolio, "action-1", reason="again")).action_id == record.action_id
    assert asyncio.run(actions.abandon(account.portfolio, "never-asked", reason="nothing")) is None


# --- what the Trader is shown, older databases and protection ------------------------------------------------------


def test_positions_list_each_coins_exits_and_its_last_action(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    actions = DayPlanActions(account.execution)
    assert actions.positions(account.portfolio) == []
    filled(account, TWO_ENTRIES, bid="98.9", ask="99")
    (sol,) = actions.positions(account.portfolio)
    assert (sol.symbol, sol.held, sol.protected, sol.unprotected, sol.resting_entries) == (
        "SOL/USD", D("8"), D("8"), D("0"), 0)
    assert sorted((item.stop_price, item.take_profit_price, item.quantity) for item in sol.exits) == [
        (D("96"), None, D("3")), (D("97"), D("104"), D("5"))]
    assert sol.last_action is None
    act(account, position_action("move_exit", stop_price="98", take_profit_price="103"))
    (sol,) = actions.positions(account.portfolio)
    assert [(item.stop_price, item.take_profit_price, item.quantity) for item in sol.exits] == [
        (D("98"), D("103"), D("8"))]
    assert (sol.last_action.action, sol.last_action.status) == ("move_exit", "done")
    act(account, position_action("close"), request_id="action-2")
    fill_quote(account, bid="98.8", ask="98.9", volume=None)
    assert actions.positions(account.portfolio) == []


def test_a_database_before_migration_0044_protects_fills_and_refuses_actions(tmp_path, monkeypatch) -> None:
    from trade_graph.adapters.persistence import migrate

    monkeypatch.setattr(migrate, "STATEMENTS", [(version, statements) for version, statements in migrate.STATEMENTS
                                                if version < "0044"])
    account = day_plan_account(tmp_path)
    assert not account.database.execute("SELECT 1 FROM sqlite_master WHERE name = 'day_plan_actions'").fetchone()
    filled(account)
    assert live_exits(account) == [(D("97"), D("104"), D("5"))]
    assert manage(account)["protected"] == []
    actions = DayPlanActions(account.execution)
    assert not actions.available()
    with pytest.raises(StaleState, match="0044"):
        act(account, position_action("close"), actions=actions)
    (sol,) = actions.positions(account.portfolio)
    assert sol.protected == D("5") and sol.last_action is None


def test_the_action_module_is_a_protected_path() -> None:
    """It holds the cancel, reconcile, sell or re-protect sequence: an execution control agents cannot edit."""
    for path in ("src/trade_graph/application/day_plan_actions.py",
                 "src/trade_graph/adapters/persistence/day_plan_action_schema.py"):
        assert path_is_protected(path)


def test_selling_never_exceeds_what_is_held(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    act(account, position_action("close"))
    (sell,) = market_sells(account)
    assert D(sell["quantity"]) <= account.held("SOL")
    assert all(order["side"] == "sell" for order in broker_orders(account, side="sell"))


def test_an_unfinished_close_whose_sell_was_never_sent_never_sells_once_it_is_abandoned(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    filled(account)
    actions = DayPlanActions(account.execution)
    dispatch = account.execution.dispatch

    async def stop_on_targeted_send(**options):
        if options.get("intent_ids"):
            raise RuntimeError("synthetic: the process stopped here")
        return await dispatch(**options)

    with monkeypatch.context() as patch:
        patch.setattr(account.execution, "dispatch", stop_on_targeted_send)
        with pytest.raises(RuntimeError, match="stopped here"):
            act(account, position_action("close"), actions=actions)
    sell_id = actions.for_request(account.portfolio, "action-1").steps[-1]["document"]["intent_id"]
    assert account.state(sell_id) == "SUBMISSION_PENDING"
    record = asyncio.run(actions.abandon(account.portfolio, "action-1",
                                         reason="the trading day ended before it finished"))
    assert record.status == "failed" and account.state(sell_id) == "REJECTED"  # abandoned, never sent
    account.dispatch()  # the service's next dispatch has nothing of it to send
    assert market_sells(account) == [] and live_exits(account) == [(D("97"), D("104"), D("5"))]
    assert account.held("SOL") == D("5")
