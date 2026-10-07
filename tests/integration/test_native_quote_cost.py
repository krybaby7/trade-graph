"""Exact synthetic native principal through protected books and cold execution recovery."""

import asyncio
import copy
import json
from decimal import Decimal, localcontext

import pytest
from tests.integration.test_kraken_live_adapter import NOW, ScriptedRest, _broker, _intent, _ledgers, _order, _trade

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.broker_identity import DurableBrokerIdentity
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.books import Books, BooksError, Lot, apply_fill

QTY = Decimal("0.12345678")


def _native(rest, index=1, *, quantity=QTY, price="100.1", cost="12.36", side="buy", fee="0.08"):
    trade = _trade(index, side=side, fee=fee)
    trade.update(vol=str(quantity), price=price, cost=cost, ordertxid="order-1",
                 time=str(Decimal("1767225600") + index - 1))
    ledgers = _ledgers(index, side=side, fee=fee)
    ledgers[f"base-{index}"].update(amount=str(quantity if side == "buy" else -quantity))
    ledgers[f"quote-{index}"].update(amount=cost if side == "sell" else "-" + cost)
    rest.results["TradesHistory"]["trades"][f"trade-{index}"] = trade
    rest.results["TradesHistory"]["count"] = len(rest.results["TradesHistory"]["trades"])
    rest.results["QueryLedgers"].update(ledgers)


def _cost_broker(rest=None, **changes):
    rest = rest or ScriptedRest()
    rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"] = 2
    clock = changes.pop("clock", None)
    broker = _broker(rest, **changes)
    if clock is not None:
        broker.clock = clock
    return rest, broker


def _stack(path, *, quantity=QTY, limit="101", reservation="30", side="buy"):
    clock = FrozenClock(NOW)
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("100"), "synthetic-opening")
    intent = _intent(portfolio_id=portfolio, quantity=str(quantity), limit_price=limit, side=side)
    asset = "USD" if side == "buy" else "BTC"
    payload = intent.model_dump(mode="json")
    payload.update(venue_order_id="order-1", reserve_asset=asset, reserve_amount=reservation)
    database.execute("INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?)", (
        intent.intent_id, portfolio, intent.client_order_id, "UNKNOWN", intent.symbol,
        json.dumps(payload), utc_iso(clock.now()), utc_iso(clock.now()),
    ))
    database.execute("INSERT INTO position_reservations VALUES (?,?,?,?,?,?,?)", (
        "synthetic-reserve", portfolio, intent.intent_id, asset, reservation, "held", utc_iso(clock.now()),
    ))
    return database, clock, ledger, portfolio, intent


def _execution(database, clock, ledger, rest):
    _, broker = _cost_broker(rest, clock=clock, intent_resolver=DurableBrokerIdentity(
        database, venue="kraken", account_id="synthetic-account", mode="live",
    ))
    execution = Execution(database, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live")
    execution.register_instrument(asyncio.run(broker.instruments())[0])
    return execution


def test_legacy_fill_json_identity_and_new_native_principal_round_trip():
    rest, broker = _cost_broker()
    _native(rest)
    fill = asyncio.run(broker.fills_since(None)).fills[0]
    assert fill.quantity == QTY and fill.price == Decimal("100.1")
    assert fill.quote_cost == fill.quote_principal == Decimal("12.36")
    assert json.loads(fill.model_dump_json())["quote_cost"] == "12.36"
    assert FillRecord.model_validate_json(fill.model_dump_json()) == fill
    legacy = fill.model_copy(update={"quote_cost": None})
    old_json = legacy.model_dump_json()
    assert "quote_cost" not in old_json
    assert FillRecord.model_validate_json(old_json).model_dump_json() == old_json
    assert legacy.quote_principal == QTY * Decimal("100.1")


@pytest.mark.parametrize("cost", ["0", "-1", "NaN", "Infinity", 0.1, True])
def test_native_principal_contract_rejects_nonpositive_nonfinite_or_float(cost):
    rest, broker = _cost_broker()
    _native(rest)
    fill = asyncio.run(broker.fills_since(None)).fills[0]
    with pytest.raises(ValueError):
        FillRecord.model_validate(dict(fill.model_dump(mode="json"), quote_cost=cost))


def test_native_buy_sell_quote_fees_conserve_exact_cash_basis_and_journal_after_restart(tmp_path):
    clock = FrozenClock(NOW)
    path = tmp_path / "native-books.sqlite"
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("100"), "opening")
    rest, broker = _cost_broker()
    _native(rest)
    buy = asyncio.run(broker.fills_since(None)).fills[0]
    ledger.apply_fill(portfolio, buy, base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("87.56")
    assert ledger.books(portfolio).lots[0].cost_original == Decimal("12.44")
    clock.advance(1)
    rest, broker = _cost_broker(clock=clock)
    _native(rest, 2, side="sell", price="101.1", cost="12.48")
    sale = asyncio.run(broker.fills_since(None)).fills[0]
    ledger.apply_fill(portfolio, sale, base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("99.96")
    assert ledger.books(portfolio).lots[0].open_quantity() == 0
    assert ledger.books(portfolio).lots[0].disposals[0].realized == Decimal("-0.04")
    assert ledger.journal_balanced(portfolio)
    database.close()
    database = Database(path)
    recovered = Ledger(database, clock)
    assert recovered.books(portfolio).cash_amount("USD") == Decimal("99.96")
    assert recovered.books(portfolio).lots[0].disposals[0].realized == Decimal("-0.04")
    assert recovered.journal_balanced(portfolio)
    database.close()


def test_rounded_native_cost_and_base_fee_preserve_inventory_without_quote_fee_invention(tmp_path):
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "base-fee.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("100"), "opening")
    rest, broker = _cost_broker()
    _native(rest, fee="0.001001")
    rest.results["QueryLedgers"]["base-1"]["fee"] = "0.00001"
    rest.results["QueryLedgers"]["quote-1"]["fee"] = "0"
    fill = asyncio.run(broker.fills_since(None)).fills[0]
    assert fill.fee_asset == "BTC" and fill.fee_amount == Decimal("0.00001")
    ledger.apply_fill(portfolio, fill, base_asset="BTC", quote_asset="USD")
    books = ledger.books(portfolio)
    assert books.cash_amount("USD") == Decimal("87.64")
    assert books.lots[0].quantity_original == QTY - Decimal("0.00001")
    assert books.lots[0].cost_original == Decimal("12.36")
    assert ledger.journal_balanced(portfolio)
    database.close()


@pytest.mark.parametrize("problem", ["undeclared", "off_quantum", "too_far", "wrong_ledger", "oversized_cost"])
def test_native_quote_cost_refuses_unproven_precision_or_nonconserving_ledger(problem):
    rest, broker = _cost_broker()
    _native(rest)
    if problem == "undeclared":
        del rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"]
    elif problem == "off_quantum":
        rest.results["TradesHistory"]["trades"]["trade-1"]["cost"] = "12.3601"
    elif problem == "too_far":
        rest.results["TradesHistory"]["trades"]["trade-1"]["cost"] = "12.37"
    elif problem == "wrong_ledger":
        rest.results["QueryLedgers"]["quote-1"]["amount"] = "-12.35"
    else:
        rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"] = 18
        with localcontext() as context:
            context.prec = 128
            quantity, price = Decimal("1.000000000000000001"), Decimal("12345678901234567890.12345678")
            cost = (quantity * price).quantize(Decimal("1e-18"))
            _native(rest, quantity=quantity, price=str(price), cost=str(cost), fee="0")
    with pytest.raises(ValidationFailure, match="quote precision|declared|native trade legs|native fill precision"):
        asyncio.run(broker.fills_since(None))


@pytest.mark.parametrize("precision", [None, 2])
def test_unguaranteed_partial_fill_rounding_refuses_submission_without_external_order_attempt(precision):
    rest = ScriptedRest()
    if precision is None:
        del rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"]
    else:
        rest.results["AssetPairs"]["XXBTZUSD"]["cost_decimals"] = precision
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    result = asyncio.run(broker.submit(_intent()))
    assert result.status == "rejected"
    assert ("precision" if precision is None else "reserve") in result.message
    assert not any(method == "AddOrder" for method, _ in rest.calls)


def test_partial_native_reservation_and_cold_terminal_replay_use_actual_principal(tmp_path):
    path = tmp_path / "partial.sqlite"
    database, clock, ledger, portfolio, intent = _stack(path, quantity=2 * QTY)
    rest = ScriptedRest()
    _native(rest)
    rest.results["QueryOrders"] = {"order-1": _order(vol=str(2 * QTY), vol_exec=str(QTY))}
    execution = _execution(database, clock, ledger, rest)
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "PARTIALLY_FILLED"
    assert database.execute("SELECT amount FROM position_reservations").fetchone()[0] == "17.56"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("87.56")
    database.close()
    database = Database(path)
    ledger = Ledger(database, clock)
    clock.advance(1)
    _native(rest, 2)
    rest.results["QueryOrders"]["order-1"] = _order(status="closed", vol=str(2 * QTY), vol_exec=str(2 * QTY))
    execution = _execution(database, clock, ledger, rest)
    asyncio.run(execution.reconcile())
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("75.12")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 2
    assert database.execute("SELECT state FROM position_reservations").fetchone()[0] == "released"
    assert ledger.journal_balanced(portfolio)
    assert not execution._reconciliation_blocked()
    assert not any(method in {"AddOrder", "CancelOrder"} for method, _ in rest.calls)
    database.close()


def test_native_limit_reserve_discrepancy_keeps_facts_and_blocks_increases_through_restart(tmp_path):
    path = tmp_path / "incident.sqlite"
    database, clock, ledger, portfolio, intent = _stack(path, limit="100.1", reservation="12.438023678")
    rest = ScriptedRest()
    _native(rest)
    rest.results["QueryOrders"] = {"order-1": _order(status="closed", vol=str(QTY), vol_exec=str(QTY))}
    execution = _execution(database, clock, ledger, rest)
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("87.56")
    assert execution._reconciliation_blocked()
    row = database.execute(
        "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
    )
    assert set(json.loads(row.fetchone()[0])["reasons"]) == {
        "native_principal_exceeds_limit", "native_fills_exceed_original_reservation",
    }
    database.close()
    database = Database(path)
    ledger = Ledger(database, clock)
    execution = _execution(database, clock, ledger, rest)
    asyncio.run(execution.reconcile())
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    assert database.execute(
        "SELECT count(*) FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
    ).fetchone()[0] == 1
    assert execution._reconciliation_health()["state"] == "incomplete"
    assert execution._reconciliation_blocked() and ledger.journal_balanced(portfolio)
    database.close()


def test_native_sell_limit_discrepancy_preserves_native_cash_and_base_reservation(tmp_path):
    database, clock, ledger, portfolio, intent = _stack(
        tmp_path / "sell-incident.sqlite", side="sell", limit="101.1", reservation=str(QTY),
    )
    rest, broker = _cost_broker()
    _native(rest)
    opening = asyncio.run(broker.fills_since(None)).fills[0].model_copy(update={"trade_id": "opening-buy"})
    ledger.apply_fill(portfolio, opening, base_asset="BTC", quote_asset="USD")
    clock.advance(1)
    rest = ScriptedRest()
    _native(rest, 2, side="sell", price="101.1", cost="12.48")
    rest.results["QueryOrders"] = {"order-1": _order(
        status="closed", side="sell", vol=str(QTY), vol_exec=str(QTY),
    )}
    execution = _execution(database, clock, ledger, rest)
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("99.96")
    assert database.execute("SELECT amount,state FROM position_reservations").fetchone()[:] == ("0", "released")
    event = database.execute(
        "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
    ).fetchone()
    assert json.loads(event[0])["reasons"] == ["native_principal_exceeds_limit"]
    assert execution._reconciliation_blocked() and ledger.journal_balanced(portfolio)
    database.close()


def test_native_cost_duplicate_reconstructs_missing_limit_incident(tmp_path, monkeypatch):
    path = tmp_path / "missing-incident.sqlite"
    database, clock, ledger, portfolio, _ = _stack(path, limit="100.1", reservation="12.438023678")
    rest = ScriptedRest()
    _native(rest)
    execution = _execution(database, clock, ledger, rest)
    fill = asyncio.run(execution.broker.fills_since(None)).fills[0]
    # Fault-injected checkpoint: the financial fact exists without its new
    # incident projection. Replaying the identical source must repair the gate.
    monkeypatch.setattr(execution, "_record_native_cost_limits", lambda _: None)
    assert execution.record_fill(fill)
    database.close()
    database = Database(path)
    ledger = Ledger(database, clock)
    execution = _execution(database, clock, ledger, rest)
    assert not execution.record_fill(fill)
    assert not execution.record_fill(fill)
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("87.56")
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    assert database.execute(
        "SELECT count(*) FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
    ).fetchone()[0] == 1
    assert execution._reconciliation_blocked() and ledger.journal_balanced(portfolio)
    database.close()


def test_native_posting_rejects_cash_rounding_before_mutation(tmp_path):
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "wide-cash.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("1e30"), "opening")
    rest, broker = _cost_broker()
    _native(rest)
    fill = asyncio.run(broker.fills_since(None)).fills[0]
    with pytest.raises(BooksError, match="without rounding"):
        ledger.apply_fill(portfolio, fill, base_asset="BTC", quote_asset="USD")
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1e30")
    assert database.execute("SELECT count(*) FROM ledger_events WHERE kind='fill'").fetchone()[0] == 0
    database.close()


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("rate", [
    "1.00000000000000000000000000001", "1234567890123456789.123456789", "1e-27",
])
def test_native_third_asset_precision_refusal_precedes_direct_books_mutation(side, rate):
    rest, broker = _cost_broker()
    _native(rest)
    native = asyncio.run(broker.fills_since(None)).fills[0]
    fill = FillRecord.model_validate(dict(native.model_dump(mode="json"), side=side,
                                         fee_asset="FEE", fee_amount="0.123456789", fee_identified_rate=rate))
    books = Books(cash={"USD": Decimal("100")}, lots=[
        Lot("fee", "FEE", Decimal("1"), Decimal("1"), "USD", utc_iso(NOW), "opening"),
        Lot("base", "BTC", QTY, Decimal("12"), "USD", utc_iso(NOW), "opening"),
    ])
    before = copy.deepcopy(books)
    with pytest.raises(BooksError, match="without rounding"):
        apply_fill(books, fill, base_asset="BTC", quote_asset="USD", lot_id="new", at=utc_iso(NOW))
    assert books == before


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_native_third_asset_exact_valuation_preserves_cash_fee_disposal_and_basis(side):
    rest, broker = _cost_broker()
    _native(rest)
    native = asyncio.run(broker.fills_since(None)).fills[0]
    fill = FillRecord.model_validate(dict(native.model_dump(mode="json"), side=side,
                                         fee_asset="FEE", fee_amount="0.1", fee_identified_rate="2"))
    books = Books(cash={"USD": Decimal("100")}, lots=[
        Lot("fee", "FEE", Decimal("1"), Decimal("1"), "USD", utc_iso(NOW), "opening"),
        Lot("base", "BTC", QTY, Decimal("12"), "USD", utc_iso(NOW), "opening"),
    ])
    apply_fill(books, fill, base_asset="BTC", quote_asset="USD", lot_id="new", at=utc_iso(NOW))
    assert books.cash_amount("USD") == Decimal("87.64" if side == "buy" else "112.36")
    assert books.lots[0].open_quantity() == Decimal("0.9")
    assert books.lots[0].disposals[0].proceeds == Decimal("0.2")
    assert books.lots[0].disposals[0].cost_released == Decimal("0.1")
    if side == "buy":
        assert books.lots[-1].cost_original == Decimal("12.56")
    else:
        assert books.lots[1].disposals[0].proceeds == Decimal("12.16")
    for asset in {posting.asset for posting in books.groups[-1]}:
        assert sum(posting.amount for posting in books.groups[-1] if posting.asset == asset) == 0
