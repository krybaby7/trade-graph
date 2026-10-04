"""Synthetic broker history drives real FIFO/cash/recovery without any live calls."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.integration.test_execution import _decision, _quote, _rules

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    AuthorizedOrderIntent,
    BrokerCapabilities,
    FillPage,
    FillRecord,
    OrderLookupResult,
    SubmitResult,
)
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, UncertainExternal, ValidationFailure


class HistoryBroker:
    def __init__(self, *, venue="paper", account="paper", mode="paper"):
        self.venue, self.account, self.mode = venue, account, mode
        self.fee_reserve_rate = Decimal("0.008")
        self.fills = []
        self.pages = None
        self.statuses = {}
        self.calls = []
        self.looked_up = set()
        self.expected_lookups = set()

    async def capabilities(self):
        return BrokerCapabilities(venue=self.venue, mode=self.mode, client_id_lookup=True,
                                  native_amend=False, native_stop=False, native_stop_tested=False,
                                  time_in_force=["gtc"], reduce_only_flag=False, fills_pagination=True,
                                  cancel_behaviour="scripted", withdrawals=False)

    async def order_status(self, lookup):
        self.calls.append(("status", lookup.client_order_id))
        self.looked_up.add(lookup.client_order_id)
        return self.statuses.get(lookup.client_order_id, OrderLookupResult(status="unknown"))

    async def fills_since(self, cursor):
        self.calls.append(("fills", cursor))
        assert self.expected_lookups <= self.looked_up  # Identity binding precedes history reads.
        if self.pages is not None:
            return self.pages(cursor)
        return FillPage(fills=self.fills)

    async def submit(self, intent):
        self.calls.append(("submit", intent.intent_id))
        return SubmitResult(status="acknowledged", venue_order_id="synthetic-order")


def _stack(tmp_path, *, account="paper", venue="paper", mode="paper"):
    database = Database(tmp_path / "reconciliation.sqlite")
    clock = FrozenClock(datetime(2026, 1, 1, 1, tzinfo=UTC))
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode=mode)
    ledger.deposit(portfolio, "USD", Decimal("1000"), "synthetic-opening")
    broker = HistoryBroker(account=account, venue=venue, mode=mode)
    execution = Execution(database, ledger, clock, broker, account_id=account, venue=venue, mode=mode)
    return database, clock, ledger, portfolio, broker, execution


def _intent(database, clock, portfolio, name, *, quantity="1", side="buy", status="OPEN",
            account="paper", venue="paper", mode="paper"):
    intent = AuthorizedOrderIntent(
        intent_id=name, portfolio_id=portfolio, account_id=account, venue=venue, mode=mode,
        client_order_id="client-" + name, symbol="BTC/USD", side=side, order_type="market",
        snapshot_id="synthetic-snapshot",
        quantity=quantity, time_in_force="gtc", eligible_after_utc=clock.now(), reduce_only=side == "sell",
    )
    reserve_asset = "USD" if side == "buy" else "BTC"
    payload = intent.model_dump(mode="json") | {"reserve_asset": reserve_asset, "reserve_amount": quantity}
    database.execute("INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
        name, portfolio, intent.client_order_id, status, intent.symbol, json.dumps(payload),
        utc_iso(clock.now()), utc_iso(clock.now()),
    ))
    database.execute("INSERT INTO position_reservations VALUES (?, ?, ?, ?, ?, 'held', ?)", (
        "reserve-" + name, portfolio, name, reserve_asset, quantity, utc_iso(clock.now()),
    ))
    if status == "SUBMISSION_PENDING":
        database.execute("INSERT INTO outbox VALUES (?, ?, 'submit', ?, '{}', 'pending', ?)", (
            "outbox-" + name, portfolio, name, utc_iso(clock.now()),
        ))
    return intent


def _fill(clock, intent, trade_id, *, minute=1, quantity="1", price="100", **updates):
    body = dict(venue=intent.venue, account_id=intent.account_id, trade_id=trade_id, intent_id=intent.intent_id,
                symbol=intent.symbol, side=intent.side, quantity=quantity, price=price, fee_amount="0",
                fee_asset="USD", liquidity="taker", filled_at_utc=clock.now() - timedelta(minutes=60 - minute))
    body.update(updates)
    return FillRecord(**body)


def test_first_clean_live_scan_and_later_unchanged_scan_each_retain_fresh_observation(tmp_path):
    database, clock, _ledger, _portfolio, broker, execution = _stack(
        tmp_path, account="synthetic-live-account", venue="kraken", mode="live",
    )
    asyncio.run(execution.reconcile())
    first = database.execute(
        "SELECT payload_json,created_at FROM activity_events WHERE kind='execution_reconciliation_health'",
    ).fetchone()
    assert first is not None
    assert json.loads(first["payload_json"])["observation_scope"] == "owned_intent_fill_history"
    assert json.loads(first["payload_json"])["state"] == "complete"
    assert broker.calls == [("fills", None)]
    clock.advance(2)
    asyncio.run(execution.reconcile())
    observations = database.execute(
        "SELECT payload_json,created_at FROM activity_events "
        "WHERE kind='execution_reconciliation_health' ORDER BY rowid",
    ).fetchall()
    assert len(observations) == 2
    assert observations[1]["created_at"] > first["created_at"]
    assert json.loads(observations[1]["payload_json"])["observed_at"] == utc_iso(clock.now())
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_reconciliation_books_interleaved_intents_in_global_fifo_order_and_survives_restart(tmp_path, monkeypatch):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    # Restored durable intents are deliberately in a different order from fills.
    first = _intent(database, clock, portfolio, "split-buy", quantity="2", status="UNKNOWN")
    sell = _intent(database, clock, portfolio, "sell", side="sell", status="SUBMITTING")
    other = _intent(database, clock, portfolio, "other-buy", status="OPEN")
    broker.expected_lookups = {intent.client_order_id for intent in (first, sell, other)}
    for intent in (first, sell, other):
        broker.statuses[intent.client_order_id] = OrderLookupResult(
            status="filled", filled_quantity=intent.quantity, venue_order_id="venue-" + intent.intent_id,
        )
    # Account history pages need not already be chronological; the whole stream
    # must be sorted before any ledger write, rather than selecting one intent.
    older = _fill(clock, first, "first-buy", minute=1, price="100")
    middle = _fill(clock, other, "middle-buy", minute=2, price="150")
    sale = _fill(clock, sell, "sale", minute=3, price="200")
    later = _fill(clock, first, "later-buy", minute=4, price="300")
    broker.pages = lambda cursor: (FillPage(fills=[later, sale], next_cursor="next") if cursor is None
                                   else FillPage(fills=[middle, older]))
    identifiers = iter(uuid.UUID(int=value) for value in range(1000, 0, -1))
    monkeypatch.setattr(uuid, "uuid4", lambda: next(identifiers))
    asyncio.run(execution.reconcile())
    assert [kind for kind, _ in broker.calls] == ["status", "status", "status", "fills", "fills"]
    books = ledger.books(portfolio)
    assert books.cash_amount("USD") == Decimal("650")
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("2")
    assert [lot.cost_original for lot in books.lots if lot.open_quantity()] == [Decimal("150"), Decimal("300")]
    assert books.lots[0].lot_id > books.lots[1].lot_id  # UUID sorting would choose the wrong FIFO lot.
    disposals = [disposal for lot in books.lots for disposal in lot.disposals]
    assert sum((disposal.proceeds - disposal.cost_released for disposal in disposals), Decimal("0")) == Decimal("100")
    assert [row[0] for row in database.execute("SELECT trade_id FROM fills ORDER BY rowid")] == [
        "first-buy", "middle-buy", "sale", "later-buy",
    ]
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state='held'").fetchone()[0] == 0
    database.close()
    recovered = Database(tmp_path / "reconciliation.sqlite")
    restarted = Execution(recovered, Ledger(recovered, clock), clock, broker)
    broker.calls.clear()
    asyncio.run(restarted.startup())
    restart_books = restarted.ledger.books(portfolio)
    assert restart_books.cash_amount("USD") == Decimal("650")
    assert [lot.cost_original for lot in restart_books.lots if lot.open_quantity()] == [Decimal("150"), Decimal("300")]
    assert recovered.execute("SELECT count(*) FROM fills").fetchone()[0] == 4
    assert broker.calls == [("fills", None), ("fills", "next")]
    recovered.close()


def test_equal_dto_timestamps_preserve_adapter_order_instead_of_trade_id_order(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    sell = _intent(database, clock, portfolio, "sell", side="sell")
    buy = _intent(database, clock, portfolio, "buy")
    for intent in (buy, sell):
        broker.statuses[intent.client_order_id] = OrderLookupResult(status="filled", filled_quantity="1")
    broker.fills = [_fill(clock, buy, "z-buy", price="100"), _fill(clock, sell, "a-sell", price="120")]
    asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1020")
    assert [row[0] for row in database.execute("SELECT trade_id FROM fills ORDER BY rowid")] == ["z-buy", "a-sell"]


def test_fifo_ties_preserve_history_event_sequence_despite_reversed_lot_ids(tmp_path, monkeypatch):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    sell = _intent(database, clock, portfolio, "sell", side="sell")
    second = _intent(database, clock, portfolio, "second")
    first = _intent(database, clock, portfolio, "first")
    broker.fills = [_fill(clock, first, "z-first", price="100"),
                    _fill(clock, second, "a-second", price="150"),
                    _fill(clock, sell, "sale", price="200")]
    identifiers = iter(uuid.UUID(int=value) for value in range(1000, 0, -1))
    monkeypatch.setattr(uuid, "uuid4", lambda: next(identifiers))
    asyncio.run(execution.reconcile())
    books = ledger.books(portfolio)
    assert [lot.cost_original for lot in books.lots if lot.open_quantity()] == [Decimal("150")]
    assert sum((item.realized for lot in books.lots for item in lot.disposals), Decimal("0")) == Decimal("100")
    assert books.lots[0].lot_id > books.lots[1].lot_id
    asyncio.run(execution.reconcile())
    assert [lot.cost_original for lot in ledger.books(portfolio).lots if lot.open_quantity()] == [Decimal("150")]


@pytest.mark.parametrize("same_instant", [False, True])
def test_late_history_cannot_silently_reorder_previously_booked_fifo(tmp_path, same_instant):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    earlier = _intent(database, clock, portfolio, "late-discovered", status="CANCELLED")
    buy = _intent(database, clock, portfolio, "buy")
    sell = _intent(database, clock, portfolio, "sell", side="sell")
    broker.fills = [_fill(clock, buy, "recorded-buy", minute=2, price="200"),
                    _fill(clock, sell, "recorded-sale", minute=3, price="300")]
    asyncio.run(execution.reconcile())
    before = ledger.books(portfolio)
    late = _fill(clock, earlier, "earlier-buy", minute=3 if same_instant else 1, price="100")
    broker.fills.insert(1 if same_instant else 0, late)
    with pytest.raises(ValidationFailure, match="chronological ledger replay"):
        asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash == before.cash
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 2
    assert execution.intent_state(earlier.intent_id) == "CANCELLED"
    assert database.execute(
        "SELECT state FROM position_reservations WHERE intent_id=?", (earlier.intent_id,),
    ).fetchone()[0] == "held"
    assert execution._reconciliation_blocked()


def test_changed_recorded_history_fails_closed_and_cannot_clear_account_health(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "known")
    broker.fills = [_fill(clock, intent, "same-trade")]
    asyncio.run(execution.reconcile())
    broker.fills = [_fill(clock, intent, "same-trade", price="200")]
    with pytest.raises(ValidationFailure, match="conflicts with recorded history"):
        asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("900")
    assert execution._reconciliation_blocked()


def test_live_account_without_any_owned_intents_still_records_unmatched_history_health(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path, mode="live", venue="kraken")
    broker.fills = [FillRecord(
        venue="kraken", account_id="paper", intent_id=None, trade_id="manual-trade", symbol="BTC/USD",
        side="buy", quantity="1", price="100", fee_amount="0", fee_asset="USD", liquidity="taker",
        filled_at_utc=clock.now(),
    )]
    with pytest.raises(UncertainExternal, match="unowned fills"):
        asyncio.run(execution.startup())
    assert broker.calls == [("fills", None)]
    assert execution._reconciliation_blocked()
    with pytest.raises(StaleState, match="account reconciliation"):
        execution.authorize(portfolio, _decision(clock, portfolio, mode="live"))
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 0


def test_historical_terminal_intent_late_fill_is_recorded_once(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "late", status="CANCELLED")
    broker.fills = [_fill(clock, intent, "late-fill")]
    asyncio.run(execution.reconcile())
    asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("900")
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert broker.calls == [("fills", None), ("fills", None)]
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 1


def test_incomplete_global_page_history_cannot_partially_release_owned_reservations(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "waiting", status="UNKNOWN")
    broker.statuses[intent.client_order_id] = OrderLookupResult(status="filled", filled_quantity="1")

    def pages(cursor):
        if cursor is None:
            return FillPage(fills=[_fill(clock, intent, "partial-history")], next_cursor="next")
        raise UncertainExternal("synthetic pagination outage")

    broker.pages = pages
    with pytest.raises(UncertainExternal):
        asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    assert execution.intent_state(intent.intent_id) == "UNKNOWN"
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state='held'").fetchone()[0] == 1


def test_unowned_history_keeps_a_durable_reconciliation_limitation_and_does_not_book_manual_fills(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "known")
    broker.statuses[intent.client_order_id] = OrderLookupResult(status="filled", filled_quantity="1")
    broker.fills = [_fill(clock, intent, "known-fill"), _fill(clock, intent, "manual-fill", minute=2, intent_id=None)]
    for _ in range(2):
        with pytest.raises(UncertainExternal, match="unowned fills"):
            asyncio.run(execution.startup())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("900")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    assert database.execute(
        "SELECT count(*) FROM activity_events WHERE kind='unreconciled_broker_fill'",
    ).fetchone()[0] == 1
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_unowned_history_blocks_direct_increases_across_restart_until_same_account_is_resolved(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    known = _intent(database, clock, portfolio, "known")
    pending = _intent(database, clock, portfolio, "pending", status="SUBMISSION_PENDING", quantity="0.01")
    broker.statuses[known.client_order_id] = OrderLookupResult(status="filled", filled_quantity="1")
    broker.fills = [_fill(clock, known, "known-fill"), _fill(clock, known, "manual-fill", minute=2, intent_id=None)]
    with pytest.raises(UncertainExternal):
        asyncio.run(execution.reconcile())
    with pytest.raises(StaleState, match="account reconciliation"):
        execution.authorize(portfolio, _decision(clock, portfolio))
    assert asyncio.run(execution.dispatch()) == 0
    assert execution.intent_state(pending.intent_id) == "SUBMISSION_PENDING"
    database.close()

    recovered = Database(tmp_path / "reconciliation.sqlite")
    ledger = Ledger(recovered, clock)
    restarted = Execution(recovered, ledger, clock, broker)
    with pytest.raises(StaleState, match="account reconciliation"):
        restarted.authorize(portfolio, _decision(clock, portfolio))
    # A successful full sweep for another account cannot clear this account's latch.
    _intent(recovered, clock, portfolio, "other-history", account="other", status="CANCELLED")
    other_broker = HistoryBroker(account="other")
    other = Execution(recovered, ledger, clock, other_broker, account_id="other")
    asyncio.run(other.reconcile())
    assert restarted._reconciliation_blocked()

    # Protective reductions remain dispatchable while increases are held.
    reduction = _intent(recovered, clock, portfolio, "protect", side="sell", status="SUBMISSION_PENDING")
    broker.calls.clear()
    assert asyncio.run(restarted.dispatch()) == 1
    assert broker.calls == [("submit", reduction.intent_id)]
    assert restarted.intent_state(pending.intent_id) == "SUBMISSION_PENDING"
    broker.pages = lambda cursor: (_ for _ in ()).throw(UncertainExternal("synthetic outage"))
    with pytest.raises(UncertainExternal):
        asyncio.run(restarted.reconcile())
    assert restarted._reconciliation_blocked()

    # Only a full history whose manual fill is now tied to a durable intent clears it.
    resolved = _intent(recovered, clock, portfolio, "resolved-manual", status="CANCELLED")
    broker.pages = None
    broker.fills[1] = _fill(clock, resolved, "manual-fill", minute=2)
    asyncio.run(restarted.reconcile())
    assert not restarted._reconciliation_blocked()
    seed_paper_authority(recovered, clock, portfolio)
    restarted.register_instrument(_rules())
    restarted.save_observation(_quote(clock, "99", "100"))
    ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="synthetic-resolution")
    assert restarted.authorize(portfolio, _decision(clock, portfolio, record_id="after-resolution"))
    assert ledger.activity_intact()


def test_malformed_owned_history_rolls_back_the_entire_fill_batch_and_retains_reservations(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    intent = _intent(database, clock, portfolio, "known")
    broker.statuses[intent.client_order_id] = OrderLookupResult(status="filled", filled_quantity="1")
    broker.fills = [_fill(clock, intent, "known-fill"), _fill(clock, intent, "bad-fill", minute=2, account_id="other")]
    with pytest.raises(ValidationFailure, match="authorized intent"):
        asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    assert execution.intent_state(intent.intent_id) == "OPEN"
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state='held'").fetchone()[0] == 1


def test_execution_queries_and_effects_do_not_cross_venue_account_or_mode_scope(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    own = _intent(database, clock, portfolio, "owned", side="sell", status="SUBMISSION_PENDING")
    for name, changes in (("other-account", {"account": "other"}), ("other-venue", {"venue": "elsewhere"}),
                          ("other-mode", {"mode": "live"})):
        _intent(database, clock, portfolio, name, status="SUBMISSION_PENDING", **changes)
    asyncio.run(execution.dispatch())
    assert broker.calls == [("submit", own.intent_id)]
    broker.calls.clear()
    broker.fills = []
    asyncio.run(execution.reconcile())
    assert broker.calls == [("status", own.client_order_id), ("fills", None)]
    for name in ("other-account", "other-venue", "other-mode"):
        assert execution.intent_state(name) == "SUBMISSION_PENDING"


def test_current_account_fill_cannot_claim_another_accounts_owned_intent(tmp_path):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    own = _intent(database, clock, portfolio, "owned")
    other = _intent(database, clock, portfolio, "other", account="other")
    broker.fills = [_fill(clock, own, "wrong-reference", intent_id=other.intent_id)]
    with pytest.raises(ValidationFailure, match="another execution scope"):
        asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["enter", "exit"])
def test_cold_live_fee_readiness_refuses_authorization_before_policy_or_financial_writes(tmp_path, action):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path, mode="live", venue="kraken")
    broker.fee_reserve_rate = None
    # Cold construction remains available for read-only unknown-order recovery.
    execution = Execution(database, ledger, clock, broker, mode="live", venue="kraken")
    before = database.execute("SELECT count(*) FROM activity_events").fetchone()[0]
    with pytest.raises(ValidationFailure, match="fee readiness"):
        execution.authorize(portfolio, _decision(clock, portfolio, mode="live", action=action))
    for table in ("decisions", "order_intents", "position_reservations", "outbox"):
        assert database.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM activity_events").fetchone()[0] == before
    assert broker.calls == []


@pytest.mark.parametrize("bound", [None, Decimal("0.02")])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_live_dispatch_rechecks_missing_or_increased_fee_bound_before_any_order_attempt(tmp_path, bound, side):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path, mode="live", venue="kraken")
    intent = _intent(database, clock, portfolio, "waiting", mode="live", venue="kraken",
                     status="SUBMISSION_PENDING", side=side)
    broker.fee_reserve_rate = bound
    assert asyncio.run(execution.dispatch()) == 0
    assert execution.intent_state(intent.intent_id) == "REJECTED"
    assert database.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state='held'").fetchone()[0] == 0
    assert broker.calls == []


@pytest.mark.parametrize("operation", ["cancel", "replace"])
@pytest.mark.parametrize("changes", [{"account": "other"}, {"venue": "other"}, {"mode": "live"}])
def test_explicit_order_commands_refuse_another_execution_binding_before_any_effect(tmp_path, operation, changes):
    database, clock, ledger, portfolio, broker, execution = _stack(tmp_path)
    foreign = _intent(database, clock, portfolio, "foreign", **changes)
    before = database.execute("SELECT payload_json FROM order_intents WHERE intent_id='foreign'").fetchone()[0]
    command = execution.cancel(foreign.intent_id) if operation == "cancel" else execution.replace(
        foreign.intent_id, _decision(clock, portfolio),
    )
    with pytest.raises(AuthorityDenied, match="another execution scope"):
        asyncio.run(command)
    assert execution.intent_state(foreign.intent_id) == "OPEN"
    assert database.execute("SELECT payload_json FROM order_intents WHERE intent_id='foreign'").fetchone()[0] == before
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state='held'").fetchone()[0] == 1
    assert database.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1000")
    assert broker.calls == []


@pytest.mark.parametrize("binding", [
    {"mode": "live", "venue": "kraken"}, {"mode": "replay"}, {"venue": "other"}, {"account_id": "other"},
])
def test_paper_protection_refuses_nondefault_binding_before_any_financial_write(tmp_path, binding):
    database, clock, ledger, portfolio, broker, _execution = _stack(tmp_path)
    execution = Execution(database, ledger, clock, broker, **binding)
    with pytest.raises(AuthorityDenied, match="paper execution binding"):
        execution.place_protection(portfolio, "BTC/USD", Decimal("1"), Decimal("90"), "synthetic-snapshot")
    for table in ("order_intents", "position_reservations", "outbox", "order_attempts"):
        assert database.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert broker.calls == []


def test_paper_protection_refuses_live_portfolio_even_with_paper_execution(tmp_path):
    database, clock, ledger, portfolio, broker, _execution = _stack(tmp_path, mode="live")
    execution = Execution(database, ledger, clock, broker)
    with pytest.raises(AuthorityDenied, match="paper portfolio"):
        execution.place_protection(portfolio, "BTC/USD", Decimal("1"), Decimal("90"), "synthetic-snapshot")
    for table in ("order_intents", "position_reservations", "outbox", "order_attempts"):
        assert database.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    assert broker.calls == []
