"""Synthetic signed per-ledger fee facts conserve every native asset across recovery."""

import asyncio
import copy
import json
from decimal import Decimal, localcontext

import pytest
from tests.integration.test_kraken_live_adapter import NOW, ScriptedRest, _broker, _ledgers, _trade
from tests.integration.test_native_quote_cost import _execution, _stack

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import FillFeeRecord, FillRecord
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.books import Books, BooksError, Lot, apply_fill


def _leg(asset, amount, ref, **updates):
    return FillFeeRecord(asset=asset, amount=amount, source_ref=ref, effective_at_utc=NOW, **updates)


def _fill(*components, side="buy", **updates):
    body = dict(
        venue="kraken",
        account_id="synthetic-account",
        trade_id="native-vector",
        intent_id=None,
        symbol="BTC/USD",
        side=side,
        quantity="1",
        price="10",
        quote_cost="10",
        fee_amount="0",
        fee_asset="USD",
        liquidity="taker",
        filled_at_utc=NOW,
        fee_components=components,
    )
    body.update(updates)
    return FillRecord(**body)


def _apply(books, fill, lot="buy"):
    apply_fill(books, fill, base_asset="BTC", quote_asset="USD", lot_id=lot, at=utc_iso(NOW))


def _owned(books, asset):
    return sum((lot.open_quantity() for lot in books.lots if lot.asset == asset), Decimal("0"))


def _balanced(books):
    for group in books.groups:
        for asset in {post.asset for post in group}:
            assert sum((post.amount for post in group if post.asset == asset), Decimal("0")) == 0


def test_vector_round_trip_preserves_separate_same_asset_native_sources_and_legacy_bytes():
    fill = _fill(_leg("USD", "0.1", "first"), _leg("USD", "-0.03", "second"))
    assert FillRecord.model_validate_json(fill.model_dump_json()) == fill
    assert [item.source_ref for item in fill.fee_legs()] == ["first", "second"]
    old = fill.model_copy(update={"fee_components": None, "fee_amount": Decimal("0.07")})
    assert "fee_components" not in old.model_dump_json()
    assert FillRecord.model_validate_json(old.model_dump_json()).model_dump_json() == old.model_dump_json()
    assert old.fee_legs()[0].amount == Decimal("0.07")


@pytest.mark.parametrize(
    "updates",
    [
        {"amount": "0"},
        {"amount": "NaN"},
        {"amount": 0.1},
        {"identified_rate": "0", "rate_source_ref": "rate"},
        {"identified_rate": "1"},
        {"rate_source_ref": "rate"},
        {"asset": "usd"},
        {"source_ref": ""},
        {"effective_at_utc": "2026-01-01T00:00:00"},
    ],
)
def test_fee_component_rejects_unsupported_numeric_or_provenance_shape(updates):
    source = dict(asset="USD", amount="-0.1", source_ref="ledger", effective_at_utc=NOW)
    with pytest.raises(ValueError):
        FillFeeRecord(**(source | updates))


@pytest.mark.parametrize(
    "changes",
    [
        {"fee_amount": "0.1"},
        {"fee_identified_rate": "1"},
        {"fee_components": (_leg("USD", "0.1", "same"), _leg("BTC", "0.01", "same"))},
        {"filled_at_utc": "2026-01-01T00:00:01Z"},
        {"fee_components": ()},
    ],
)
def test_vector_refuses_double_fee_or_duplicate_or_late_sources(changes):
    with pytest.raises(ValueError):
        _fill(_leg("USD", "0.1", "ledger"), **changes)


def test_buy_multiple_charge_and_rebate_assets_have_exact_cash_and_basis():
    books = Books(
        cash={"USD": Decimal("100")}, lots=[Lot("fee", "ETH", Decimal("2"), Decimal("4"), "USD", "0", "seed")]
    )
    _apply(
        books,
        _fill(
            _leg("USD", "0.2", "quote"),
            _leg("USD", "-0.05", "quote-rebate"),
            _leg("BTC", "0.01", "base"),
            _leg("ETH", "0.5", "third", identified_rate="3", rate_source_ref="fx"),
        ),
    )
    assert books.cash_amount("USD") == Decimal("89.85")
    assert _owned(books, "BTC") == Decimal("0.99")
    assert _owned(books, "ETH") == Decimal("1.5")
    assert books.lots[-1].cost_original == Decimal("11.65")
    assert books.lots[0].disposals[0].realized == Decimal("0.5")
    _balanced(books)


def test_third_asset_rebate_acquires_identified_lot_and_reduces_main_basis_once():
    books = Books(cash={"USD": Decimal("100")})
    _apply(
        books,
        _fill(
            _leg("ETH", "-0.5", "third-credit", identified_rate="3", rate_source_ref="fx"),
            _leg("BTC", "-0.01", "base-credit"),
        ),
    )
    assert books.cash_amount("USD") == Decimal("90")
    assert _owned(books, "BTC") == Decimal("1.01")
    assert _owned(books, "ETH") == Decimal("0.5")
    assert books.lots[0].cost_original == Decimal("1.5")
    assert books.lots[-1].cost_original == Decimal("8.5")
    _balanced(books)


def test_sell_base_and_third_rebates_keep_source_lots_and_realized_cash_basis():
    books = Books(cash={"USD": Decimal("0")}, lots=[Lot("base", "BTC", Decimal("2"), Decimal("8"), "USD", "0", "seed")])
    _apply(
        books,
        _fill(
            _leg("USD", "-0.1", "quote-credit"),
            _leg("BTC", "-0.01", "base-credit"),
            _leg("ETH", "-0.5", "third-credit", identified_rate="3", rate_source_ref="fx"),
            side="sell",
        ),
    )
    assert books.cash_amount("USD") == Decimal("10.1")
    assert _owned(books, "BTC") == Decimal("1.01")
    assert _owned(books, "ETH") == Decimal("0.5")
    assert books.lots[0].disposals[0].proceeds == Decimal("11.7")
    assert books.lots[0].disposals[0].realized == Decimal("7.7")
    assert [lot.cost_original for lot in books.lots[1:]] == [Decimal("0.1"), Decimal("1.5")]
    _balanced(books)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_missing_third_asset_inventory_or_rate_is_zero_mutation(side):
    books = Books(
        cash={"USD": Decimal("100")}, lots=[Lot("base", "BTC", Decimal("2"), Decimal("8"), "USD", "0", "seed")]
    )
    prior = copy.deepcopy(books)
    with pytest.raises(BooksError, match="insufficient inventory"):
        _apply(books, _fill(_leg("ETH", "0.5", "third", identified_rate="3", rate_source_ref="fx"), side=side))
    assert books == prior
    with pytest.raises(BooksError, match="identified rate"):
        _apply(books, _fill(_leg("ETH", "0.5", "third"), side=side))
    assert books == prior


@pytest.mark.parametrize("precision", [3, 50])
def test_native_fee_exactness_independent_of_ambient_context_and_no_partial_mutation(precision):
    books = Books(cash={"USD": Decimal("100")})
    fill = _fill(_leg("USD", "0.1234567890123456789012345678", "quote"))
    prior = copy.deepcopy(books)
    with localcontext() as context:
        context.prec = precision
        with pytest.raises(BooksError, match="without rounding"):
            _apply(books, fill)
    assert books == prior
    with localcontext() as context:
        context.prec = precision
        _apply(books, _fill(_leg("USD", "0.12", "quote"), _leg("BTC", "-0.01", "base")))
    assert books.cash_amount("USD") == Decimal("89.88")
    assert books.lots[-1].quantity_original == Decimal("1.01")


def _native_vector(rest, *, signed=False):
    trade = _trade(1, fee="0.05" if signed else "0.11")
    rest.results["TradesHistory"] = {"count": 1, "trades": {"trade-1": trade}}
    ledgers = _ledgers(1, fee="-0.05" if signed else "0.01")
    ledgers["base-1"].update(fee="0.001", time=trade["time"])
    ledgers["quote-1"].update(time=trade["time"])
    rest.results["QueryLedgers"] = ledgers


@pytest.mark.parametrize("signed", [True, False])
def test_kraken_retains_native_source_fee_vector_and_signed_credit(signed):
    rest = ScriptedRest()
    _native_vector(rest, signed=signed)
    fill = asyncio.run(_broker(rest).fills_since(None)).fills[0]
    assert fill.fee_amount == 0 and fill.fee_asset == "USD"
    assert [item.source_ref for item in fill.fee_components] == ["base-1", "quote-1"]
    assert [item.amount for item in fill.fee_components] == [Decimal("0.001"), Decimal("-0.05" if signed else "0.01")]
    books = Books(cash={"USD": Decimal("100")})
    _apply(books, fill)
    assert books.cash_amount("USD") == Decimal("90.05" if signed else "89.99")
    assert _owned(books, "BTC") == Decimal("0.099")


@pytest.mark.parametrize("tamper", ["time", "nominal", "third"])
def test_kraken_vector_requires_exact_native_times_nominal_and_individual_third_rates(tamper):
    rest = ScriptedRest()
    _native_vector(rest)
    if tamper == "time":
        rest.results["QueryLedgers"]["base-1"].pop("time")
    elif tamper == "nominal":
        rest.results["TradesHistory"]["trades"]["trade-1"]["fee"] = "0.12"
    else:
        trade = rest.results["TradesHistory"]["trades"]["trade-1"]
        trade["ledgers"].append("third")
        rest.results["QueryLedgers"]["third"] = dict(
            refid="trade-1", type="trade", asset="XETH", amount="0", fee="0.01", time=trade["time"]
        )
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).fills_since(None))
    assert not any(method in {"AddOrder", "CancelOrder"} for method, _ in rest.calls)


def test_vector_restart_retains_cash_fifo_and_immutable_native_fee_sources(tmp_path):
    path = tmp_path / "vector.sqlite"
    database = Database(path)
    clock = FrozenClock(NOW)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("100"), "opening")
    fill = _fill(_leg("USD", "0.1", "quote"), _leg("BTC", "-0.01", "base-credit"))
    ledger.apply_fill(portfolio, fill, base_asset="BTC", quote_asset="USD")
    payload = database.execute("SELECT payload_json FROM ledger_events WHERE kind='fill'").fetchone()[0]
    before = ledger.books(portfolio)
    assert ledger.journal_balanced(portfolio)
    database.close()
    database = Database(path)
    ledger = Ledger(database, clock)
    assert ledger.books(portfolio) == before
    assert database.execute("SELECT payload_json FROM ledger_events WHERE kind='fill'").fetchone()[0] == payload
    database.close()


def test_vector_reservation_consumes_gross_native_debits_never_replenished_by_rebates(tmp_path):
    database, clock, ledger, portfolio, intent = _stack(
        tmp_path / "reserve.sqlite", quantity=Decimal("1"), limit="11", reservation="20"
    )
    rest = ScriptedRest()
    execution = _execution(database, clock, ledger, rest)
    fill = _fill(
        _leg("USD", "0.2", "charge"),
        _leg("USD", "-0.1", "rebate"),
        intent_id=intent.intent_id,
        quantity="0.5",
        quote_cost="5",
    )
    assert execution.record_fill(fill)
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("94.9")
    assert Decimal(database.execute("SELECT amount FROM position_reservations").fetchone()[0]) == Decimal("14.8")
    assert not execution.record_fill(fill)
    assert Decimal(database.execute("SELECT amount FROM position_reservations").fetchone()[0]) == Decimal("14.8")
    database.close()


def test_vector_over_original_reservation_keeps_financial_facts_and_sticky_incident(tmp_path):
    database, clock, ledger, portfolio, intent = _stack(
        tmp_path / "limit.sqlite", quantity=Decimal("1"), limit="11", reservation="9"
    )
    execution = _execution(database, clock, ledger, ScriptedRest())
    fill = _fill(_leg("USD", "0.2", "charge"), _leg("USD", "-0.1", "rebate"), intent_id=intent.intent_id)
    assert execution.record_fill(fill)
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("89.9")
    event = json.loads(
        database.execute(
            "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'"
        ).fetchone()[0]
    )
    assert event["financial_facts_preserved"] is True
    assert "native_fills_exceed_original_reservation" in event["reasons"]
    assert execution._reconciliation_blocked()
    assert ledger.journal_balanced(portfolio)
    database.close()


def test_dashboard_reports_each_signed_fee_source_without_legacy_double_charge(tmp_path):
    from tests.integration.test_dashboard_financial import _runtime

    from trade_graph.api import financial

    runtime = _runtime(tmp_path, capital="100", currency="USD")
    fill = _fill(_leg("USD", "0.2", "charge"), _leg("USD", "-0.1", "rebate"), _leg("BTC", "-0.01", "base-credit"))
    runtime.ledger.apply_fill(runtime.portfolio_id, fill, base_asset="BTC", quote_asset="USD")
    runtime.ledger.observe_mark(runtime.portfolio_id, "BTC", Decimal("10"), "USD", source="synthetic")
    fees = financial.overview(runtime)["performance"]["trading_fees"]
    assert {item["asset"]: item["amount"] for item in fees["native"]} == {"BTC": "-0.01", "USD": "0.1"}
    assert Decimal(fees["reporting_amount"]) == 0
    assert [item["source_ref"] for item in fees["items"]] == ["charge", "rebate", "base-credit"]
    assert [item["amount"] for item in fees["items"]] == ["0.2", "-0.1", "-0.01"]
    runtime.database.close()


def test_sell_quote_fee_draw_above_proceeds_retains_facts_and_unreserved_quote_incident(tmp_path):
    database, clock, ledger, portfolio, intent = _stack(
        tmp_path / "quote-sale.sqlite",
        quantity=Decimal("1"),
        side="sell",
        limit="9",
        reservation="1",
    )
    opening = _fill(_leg("USD", "-0.1", "opening-credit"), trade_id="opening")
    ledger.apply_fill(portfolio, opening, base_asset="BTC", quote_asset="USD")
    execution = _execution(database, clock, ledger, ScriptedRest())
    sale = _fill(_leg("USD", "11", "quote-charge"), side="sell", intent_id=intent.intent_id)
    assert execution.record_fill(sale)
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("89.1")
    payload = json.loads(
        database.execute(
            "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
        ).fetchone()[0]
    )
    assert payload["unreserved_fee_assets"] == ["USD"]
    assert payload["financial_facts_preserved"] is True
    assert execution._reconciliation_blocked()
    database.close()


def test_claimed_secondary_asset_map_without_native_hold_cannot_cover_fee_debit(tmp_path):
    database, clock, ledger, portfolio, intent = _stack(
        tmp_path / "secondary.sqlite",
        quantity=Decimal("1"),
        limit="11",
        reservation="20",
    )
    seed = _fill(_leg("USD", "-0.1", "seed-credit"), trade_id="eth-seed", symbol="ETH/USD")
    ledger.apply_fill(portfolio, seed, base_asset="ETH", quote_asset="USD")
    payload = json.loads(database.execute("SELECT payload_json FROM order_intents").fetchone()[0])
    payload["reserve_amounts"] = {"USD": "20", "ETH": "100"}
    database.execute("UPDATE order_intents SET payload_json=?", (json.dumps(payload),))
    execution = _execution(database, clock, ledger, ScriptedRest())
    fill = _fill(
        _leg("ETH", "0.5", "third-charge", identified_rate="10", rate_source_ref="native-fx"),
        intent_id=intent.intent_id,
        quantity="0.5",
        quote_cost="5",
    )
    assert execution.record_fill(fill)
    payload = json.loads(
        database.execute(
            "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
        ).fetchone()[0]
    )
    assert payload["unreserved_fee_assets"] == ["ETH"]
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("85.1")
    database.close()
