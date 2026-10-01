"""Dashboard money comes from durable accounting, with paper and actual costs separated."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.api import financial
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    Decision,
    FillRecord,
    InstrumentRules,
    ModelUsage,
    Observation,
    PriceCard,
    Quantity,
)
from trade_graph.domain.clock import FrozenClock, utc_iso


def _runtime(tmp_path, *, capital="100", currency="EUR"):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "financial.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    if currency != "EUR":
        ledger.observe_fx(
            base=currency,
            quote="EUR",
            rate=Decimal("0.9"),
            source="synthetic opening reference",
            kind="reference",
            stale=False,
        )
    ledger.deposit(portfolio, currency, Decimal(capital), "opening")
    budget = BudgetGateway(database, clock)
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("10"),
        period=Decimal("10"),
        priority_reserve=Decimal("0"),
        daily=Decimal("10"),
        root=Decimal("10"),
        roles={role: Decimal("10") for role in ("leader", "engineer", "trader")},
    )
    budget.seed_card(
        PriceCard(
            price_card_id="dashboard-synthetic-card",
            provider="scripted",
            model="scripted",
            endpoint="https://example.invalid",
            currency="USD",
            input_per_million="1",
            output_per_million="0",
            effective_at="2026-01-01",
            verified_at="2026-01-01",
            source_id="synthetic",
            tier="standard",
            context_band="short",
        )
    )
    return SimpleNamespace(
        database=database,
        ledger=ledger,
        clock=clock,
        portfolio_id=portfolio,
        budget=budget,
        deployment_id="deployment",
        # A process-local cached value must never be authoritative.
        actual_spend=Decimal("987654.32"),
    )


def _reservation(
    runtime, *, tokens=1_000_000, role="leader", synthetic=False, purpose="dashboard accounting fixture"
):
    return runtime.budget.reserve(
        deployment_id="deployment",
        role=role,
        task_id=f"{role}-task",
        root_task_id=f"{role}-root",
        price_card_id="dashboard-synthetic-card",
        max_input=tokens,
        max_output=0,
        max_tools=0,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
        priority=False,
        synthetic=synthetic,
        purpose=purpose,
        system_version_id="fixture-version",
    )


def _receipt(runtime, *, tokens=1_000_000, role="leader", synthetic=False, purpose="dashboard accounting fixture"):
    reservation = _reservation(runtime, tokens=tokens, role=role, synthetic=synthetic, purpose=purpose)
    return runtime.budget.commit(
        reservation,
        ModelUsage(
            uncached_input_tokens=tokens,
            billed_output_tokens=0,
            provider_request_id=f"fixture-{reservation}",
        ),
        provider="scripted",
        model="scripted",
        fx_rate=Decimal("0.9"),
    )


def _fill(runtime, trade_id, *, side="buy", quantity="1", price="40", fee="0.40"):
    fill = FillRecord(
        venue="paper",
        account_id="paper-fixture",
        trade_id=trade_id,
        intent_id=None,
        symbol="TEST/EUR",
        side=side,
        quantity=quantity,
        price=price,
        fee_amount=fee,
        fee_asset="EUR",
        liquidity="taker",
        filled_at_utc=runtime.clock.now(),
    )
    return runtime.ledger.apply_fill(
        runtime.portfolio_id, fill, base_asset="TEST", quote_asset="EUR"
    )


def _decimal(value):
    assert isinstance(value, str), "financial projections serialize amounts as decimal strings"
    return Decimal(value)


def _execution(runtime, *, account_id="paper"):
    broker = PaperBroker(runtime.database, runtime.clock)
    execution = Execution(runtime.database, runtime.ledger, runtime.clock, broker, account_id=account_id)
    runtime.execution = execution
    execution.register_instrument(
        InstrumentRules(
            venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
            price_increment="0.1", quantity_increment="0.001",
            min_quantity="0.001", min_notional="1", synthetic=True,
        )
    )
    seed_paper_authority(runtime.database, runtime.clock, runtime.portfolio_id)
    return execution


def _quote(runtime, observation_id, *, size="0.01", bid="99", ask="100"):
    return Observation(
        observation_id=observation_id, venue="paper", symbol="BTC/USD",
        event_time_utc=runtime.clock.now(), available_at_utc=runtime.clock.now(),
        bid=bid, ask=ask, bid_size=size, ask_size=size, source="fixture", kind="quote",
    )


def _decision(runtime, **updates):
    payload = {
        "record_id": "sizing-decision", "created_at_utc": runtime.clock.now(),
        "run_id": "trade-run", "task_id": "trade-task", "root_task_id": "trade-root",
        "portfolio_id": runtime.portfolio_id, "mode": "paper", "system_version_id": "trade-version",
        "trace_id": "trace", "action": "enter", "symbol": "BTC/USD",
        "quantity": Quantity(amount="0.0159", asset="BTC"),
        "rationale": "test sized entry", "invalidation": "below 90", "horizon_seconds": 3600,
        "strategy_id": "fixture", "snapshot_id": "obs:opening-quote",
        "mandate_revision": "1", "policy_revision": "1",
    }
    payload.update(updates)
    return Decision.model_validate(payload)


def test_costs_survive_restart_and_reset_without_counting_synthetic_or_allocations_twice(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.clock.advance(1)
    shared = _receipt(runtime)
    engineer = _receipt(runtime, tokens=200_000, role="engineer")
    synthetic = _receipt(runtime, tokens=3_000_000, synthetic=True)
    uncertain = _reservation(runtime)
    runtime.budget.mark_uncertain(uncertain)

    reset = runtime.ledger.create_portfolio(reporting_currency="EUR", reset_of=runtime.portfolio_id)
    runtime.ledger.deposit(reset, "USD", Decimal("10000"), "reset-opening")
    runtime.budget.allocate(shared, {runtime.portfolio_id: Decimal("0.25"), reset: Decimal("0.75")})
    runtime.budget.allocate(engineer, {runtime.portfolio_id: Decimal("1")})
    runtime.budget.allocate(synthetic, {runtime.portfolio_id: Decimal("0.25"), reset: Decimal("0.75")})

    before = runtime.budget.remaining("deployment")
    projected = financial.costs(runtime)
    assert _decimal(projected["actual_spend"]) == Decimal("1.08")
    assert _decimal(projected["synthetic_spend"]) == Decimal("2.7")
    assert _decimal(projected["allocated_actual_spend"]) == Decimal("0.405")
    assert _decimal(projected["remaining_allowance"]) == Decimal("8.02") == before
    assert projected["uncertain_reservations"] == 1
    assert projected["provisional"] is True
    assert _decimal(projected["summary"]["actual_accrued"]) == Decimal("1.08")
    assert _decimal(projected["summary"]["synthetic_accrued"]) == Decimal("2.7")
    assert _decimal(projected["summary"]["uncertain"]) == Decimal("0.9")
    uncertain_view = financial.overview(runtime)
    assert _decimal(uncertain_view["performance"]["trading_pnl"]) == 0
    assert uncertain_view["performance"]["provisional"] is True
    assert uncertain_view["performance"]["net_economic_pnl"] is None

    # Restart discards process caches. The same receipt still costs once globally.
    path = runtime.database.path
    runtime.database.close()
    runtime.database = Database(path)
    runtime.ledger = Ledger(runtime.database, runtime.clock)
    runtime.budget = BudgetGateway(runtime.database, runtime.clock)
    runtime.actual_spend = Decimal("0")
    restarted = financial.costs(runtime)
    assert _decimal(restarted["actual_spend"]) == Decimal("1.08")
    assert _decimal(restarted["allocated_actual_spend"]) == Decimal("0.405")
    assert _decimal(restarted["remaining_allowance"]) == before

    runtime.portfolio_id = reset
    after_reset = financial.costs(runtime)
    assert _decimal(after_reset["actual_spend"]) == Decimal("1.08")
    assert _decimal(after_reset["allocated_actual_spend"]) == Decimal("0.675")
    assert _decimal(after_reset["remaining_allowance"]) == before
    assert (
        _decimal(restarted["allocated_actual_spend"])
        + _decimal(after_reset["allocated_actual_spend"])
        == _decimal(after_reset["actual_spend"])
    )


def test_usd_capital_fx_gain_is_compared_with_the_usd_cash_benchmark(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    opening = financial.overview(runtime)
    assert _decimal(opening["equity"]) == Decimal("9000")
    assert opening["reporting_currency"] == "EUR"
    assert opening["simulated"] is True
    assert _decimal(opening["actual_spend"]) == 0

    runtime.clock.advance(60)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.91"),
        source="synthetic later reference", kind="reference", stale=False,
    )
    current = financial.overview(runtime)
    assert _decimal(current["allocated_capital"]["reporting_amount"]) == Decimal("9000")
    assert current["allocated_capital"]["native"] == [{"asset": "USD", "amount": "10000"}]
    assert _decimal(current["current_value"]["cash"]) == Decimal("9100")
    assert _decimal(current["current_value"]["inventory"]) == 0
    assert _decimal(current["current_value"]["total"]) == Decimal("9100")
    assert _decimal(current["performance"]["trading_pnl"]) == Decimal("100")
    assert _decimal(current["performance"]["net_economic_pnl"]) == Decimal("100")
    assert _decimal(current["external_flows"]["net_reporting"]) == 0
    assert _decimal(current["benchmark"]["value"]) == Decimal("9100")
    assert _decimal(current["benchmark"]["fx_pnl"]) == Decimal("100")
    assert _decimal(current["benchmark"]["strategy_alpha"]) == 0
    assert current["provisional"] is False


@pytest.mark.parametrize("embedded", [False, True])
def test_exact_trading_fixture_adds_back_embedded_cost_and_deducts_receipt_once(tmp_path, embedded):
    runtime = _runtime(tmp_path)
    runtime.clock.advance(1)
    _fill(runtime, "buy")
    runtime.clock.advance(1)
    _fill(runtime, "sell", side="sell", price="44", fee="0.44")
    runtime.clock.advance(1)
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    runtime.ledger.add_expense(
        runtime.portfolio_id,
        expense_id=receipt,
        native_amount=Decimal("0.9") if embedded else Decimal("1"),
        native_currency="EUR" if embedded else "USD",
        reporting_amount=Decimal("0.9"),
        reporting_currency="EUR",
        embedded=embedded,
        source="model-gateway",
    )
    # Owner deposits change equity, while an internal reclassification changes neither.
    runtime.clock.advance(1)
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("50"), "later-deposit")
    runtime.ledger.internal_transfer(runtime.portfolio_id, "EUR", Decimal("10"), "internal")
    current = financial.overview(runtime)
    assert _decimal(current["equity"]) == Decimal("152.26" if embedded else "153.16")
    assert _decimal(current["external_flows"]["net_reporting"]) == Decimal("50")
    performance = current["performance"]
    assert _decimal(performance["realized"]) == Decimal("3.16")
    assert _decimal(performance["unrealized"]) == 0
    assert _decimal(performance["trading_fees"]["reporting_amount"]) == Decimal("0.84")
    assert _decimal(performance["trading_pnl"]) == Decimal("3.16")
    assert _decimal(performance["embedded_operating"]) == Decimal("0.9" if embedded else "0")
    assert _decimal(performance["allocated_actual_operating"]) == Decimal("0.9")
    assert _decimal(performance["net_economic_pnl"]) == Decimal("2.26")
    assert _decimal(current["actual_spend"]) == Decimal("0.9")


def test_historical_flow_fx_is_preserved_and_stale_current_fx_degrades_results(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    runtime.clock.advance(1)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.91"),
        source="synthetic deposit reference", kind="reference", stale=False,
    )
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("100"), "later-usd-deposit")
    runtime.clock.advance(1)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.92"),
        source="synthetic current reference", kind="reference", stale=False,
    )
    current = financial.overview(runtime)
    assert _decimal(current["equity"]) == Decimal("9292")
    assert _decimal(current["external_flows"]["net_reporting"]) == Decimal("91")
    assert _decimal(current["performance"]["trading_pnl"]) == Decimal("201")
    deposit = next(item for item in current["external_flows"]["items"] if item["amount"] == "100")
    assert _decimal(deposit["reporting_amount"]) == Decimal("91")
    assert deposit["at"] == "2026-01-01T00:00:01.000000Z"

    runtime.clock.advance(1)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.93"),
        source="stale synthetic reference", kind="reference", stale=True,
    )
    stale = financial.overview(runtime)
    assert stale["provisional"] is True
    assert stale["performance"]["provisional"] is True
    # A stale value can remain visible, but cannot appear as settled performance.
    assert stale["performance"]["trading_pnl"] is None
    assert stale["performance"]["net_economic_pnl"] is None


def test_positions_show_fifo_open_lots_and_locked_funds_without_double_counting(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.clock.advance(1)
    first_lot = _fill(runtime, "first-buy")
    runtime.clock.advance(1)
    second_lot = _fill(runtime, "second-buy", price="42", fee="0.42")
    runtime.clock.advance(1)
    _fill(runtime, "partial-sale", side="sell", quantity="0.5", price="44", fee="0.22")
    runtime.ledger.observe_mark(
        runtime.portfolio_id, "TEST", Decimal("44"), "EUR", source="synthetic mid"
    )
    for reservation_id, asset, amount in (("test-hold", "TEST", "0.25"), ("cash-hold", "EUR", "10")):
        runtime.database.execute(
            """INSERT INTO position_reservations
            (reservation_id, portfolio_id, intent_id, asset, amount, state, created_at)
            VALUES (?, ?, ?, ?, ?, 'held', ?)""",
            (
                reservation_id, runtime.portfolio_id, f"intent-{reservation_id}",
                asset, amount, utc_iso(runtime.clock.now()),
            ),
        )
    projected = financial.positions(runtime)
    position = projected["positions"][0]
    assert position["asset"] == "TEST"
    assert _decimal(position["quantity"]) == Decimal("1.5")
    assert _decimal(position["reserved"]) == Decimal("0.25")
    assert _decimal(position["available"]) == Decimal("1.25")
    assert position["cost_native"] == [{"currency": "EUR", "amount": "62.62"}]
    assert _decimal(position["reporting_amount"]) == Decimal("66")
    assert _decimal(position["unrealized"]) == Decimal("3.38")
    lots = {lot["lot_id"]: lot for lot in position["lots"]}
    assert set(lots) == {first_lot, second_lot}
    assert _decimal(lots[first_lot]["quantity_original"]) == Decimal("1")
    assert _decimal(lots[first_lot]["cost_original"]) == Decimal("40.4")
    assert _decimal(lots[first_lot]["open_quantity"]) == Decimal("0.5")
    assert _decimal(lots[first_lot]["open_cost"]) == Decimal("20.2")
    assert _decimal(lots[first_lot]["disposals"][0]["realized"]) == Decimal("1.58")
    assert _decimal(lots[second_lot]["open_quantity"]) == Decimal("1")
    assert _decimal(lots[second_lot]["open_cost"]) == Decimal("42.42")
    assert lots[second_lot]["disposals"] == []
    cash = next(item for item in projected["balances"] if item["asset"] == "EUR")
    assert _decimal(cash["owned"]) == Decimal("38.96")
    assert _decimal(cash["reserved"]) == Decimal("10")
    assert _decimal(cash["available"]) == Decimal("28.96")
    assert _decimal(cash["reporting_amount"]) == Decimal("38.96")
    assert _decimal(financial.overview(runtime)["equity"]) == Decimal("104.96")


def test_orders_expose_requested_and_submitted_rounding_with_durable_fills(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    execution = _execution(runtime)
    execution.save_observation(_quote(runtime, "opening-quote"))
    decision = _decision(runtime)
    intent = execution.authorize(runtime.portfolio_id, decision)
    pending = financial.orders(runtime)["orders"][0]
    assert pending["state"] == "SUBMISSION_PENDING"
    assert _decimal(pending["requested_quantity"]) == Decimal("0.0159")
    assert _decimal(pending["submitted_quantity"]) == Decimal("0.015")
    assert _decimal(pending["rounding_delta"]) == Decimal("-0.0009")
    assert pending["fills"] == []

    asyncio.run(execution.dispatch())
    runtime.clock.advance(1)
    execution.on_observation(_quote(runtime, "fill-quote"))
    filled = financial.orders(runtime)["orders"][0]
    assert filled["state"] == "PARTIALLY_FILLED"
    assert _decimal(filled["requested_quantity"]) == Decimal("0.0159")
    assert _decimal(filled["submitted_quantity"]) == Decimal("0.015")
    assert len(filled["fills"]) == 1
    assert _decimal(filled["fills"][0]["quantity"]) == Decimal("0.01")
    assert _decimal(filled["fills"][0]["price"]) == Decimal("100")
    runtime.ledger.observe_mark(
        runtime.portfolio_id, "BTC", Decimal("100"), "USD", source="synthetic position mark"
    )
    position = financial.positions(runtime)["positions"][0]
    assert position["lots"][0]["opening_decision_id"] == "sizing-decision"
    assert position["lots"][0]["opening_version_id"] == "trade-version"
    assert position["theses"][0]["decision_id"] == "sizing-decision"
    assert position["theses"][0]["snapshot_id"] == "obs:opening-quote"

    runtime.database.execute("UPDATE order_intents SET state = 'UNKNOWN' WHERE intent_id = ?", (intent,))
    uncertain = financial.orders(runtime)["orders"][0]
    assert uncertain["degraded"] is True
    assert uncertain["state"] == "UNKNOWN"
    assert len(uncertain["fills"]) == 1


def test_receipt_pagination_does_not_change_global_financial_totals(tmp_path):
    runtime = _runtime(tmp_path)
    receipt_ids = set()
    for tokens in (100_000, 200_000, 300_000):
        runtime.clock.advance(1)
        receipt_ids.add(_receipt(runtime, tokens=tokens))
    first = financial.costs(runtime, limit=1, offset=0)
    second = financial.costs(runtime, limit=1, offset=1)
    third = financial.costs(runtime, limit=1, offset=2)
    assert len(first["receipts"]) == len(second["receipts"]) == len(third["receipts"]) == 1
    assert {page["receipts"][0]["receipt_id"] for page in (first, second, third)} == receipt_ids
    for page in (first, second, third):
        assert _decimal(page["actual_spend"]) == Decimal("0.54")
        assert _decimal(page["remaining_allowance"]) == Decimal("9.46")
    assert financial.costs(runtime, limit=1, offset=3)["receipts"] == []


def test_shared_actual_cost_is_allocated_once_and_synthetic_cost_never_reduces_economics(tmp_path):
    runtime = _runtime(tmp_path)
    second = runtime.ledger.create_portfolio(reporting_currency="EUR")
    runtime.ledger.deposit(second, "EUR", Decimal("100"), "second-opening")
    runtime.clock.advance(1)
    actual = _receipt(runtime)
    synthetic = _receipt(runtime, tokens=3_000_000, synthetic=True)
    weights = {runtime.portfolio_id: Decimal("0.25"), second: Decimal("0.75")}
    runtime.budget.allocate(actual, weights)
    runtime.budget.allocate(synthetic, weights)
    first_view = financial.overview(runtime)
    runtime.portfolio_id = second
    second_view = financial.overview(runtime)
    assert _decimal(first_view["performance"]["trading_pnl"]) == 0
    assert _decimal(second_view["performance"]["trading_pnl"]) == 0
    assert _decimal(first_view["performance"]["net_economic_pnl"]) == Decimal("-0.225")
    assert _decimal(second_view["performance"]["net_economic_pnl"]) == Decimal("-0.675")
    assert _decimal(first_view["actual_spend"]) == _decimal(second_view["actual_spend"]) == Decimal("0.9")
    assert (
        _decimal(first_view["performance"]["allocated_actual_operating"])
        + _decimal(second_view["performance"]["allocated_actual_operating"])
        == Decimal("0.9")
    )


def test_same_timestamp_deposit_is_distinguished_from_opening_by_ledger_sequence(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("50"), "same-time-owner-flow")
    current = financial.overview(runtime)
    assert _decimal(current["allocated_capital"]["reporting_amount"]) == Decimal("100")
    assert _decimal(current["external_flows"]["net_reporting"]) == Decimal("50")
    assert _decimal(current["equity"]) == Decimal("150")
    assert _decimal(current["performance"]["trading_pnl"]) == 0


def test_missing_fx_never_treats_native_usd_as_reported_eur(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    runtime.database.execute("DELETE FROM fx_rates")
    current = financial.overview(runtime)
    assert current["equity"] is None
    assert current["provisional"] is True
    assert current["allocated_capital"]["reporting_amount"] is None
    assert current["current_value"]["total"] is None
    assert current["benchmark"]["value"] is None
    assert current["performance"]["trading_pnl"] is None
    assert current["performance"]["net_economic_pnl"] is None


def test_other_operating_accruals_are_global_once_and_settlement_preserves_all_in_costs(tmp_path):
    runtime = _runtime(tmp_path)
    second = runtime.ledger.create_portfolio(reporting_currency="EUR")
    runtime.ledger.deposit(second, "EUR", Decimal("100"), "second-opening")
    runtime.clock.advance(1)
    engineer = _receipt(runtime, role="engineer", purpose="setup:failed engineering fixture")
    _receipt(runtime, tokens=200_000, role="leader")
    runtime.budget.allocate(engineer, {runtime.portfolio_id: Decimal("1")})
    for portfolio, expense_id, amount, source in (
        (runtime.portfolio_id, "hosting-accrual", "1.25", "recurring:hosting fixture"),
        (second, "research-setup", "2", "setup:research fixture"),
    ):
        runtime.ledger.add_expense(
            portfolio, expense_id=expense_id,
            native_amount=Decimal(amount), native_currency="EUR",
            reporting_amount=Decimal(amount), reporting_currency="EUR",
            embedded=False, source=source,
        )
    before = financial.costs(runtime)
    assert _decimal(before["actual_spend"]) == Decimal("4.33")
    assert _decimal(before["allocated_actual_spend"]) == Decimal("2.15")
    assert _decimal(before["summary"]["setup"]) == Decimal("2.9")
    assert _decimal(before["summary"]["recurring"]) == Decimal("1.25")
    # Role alone never relabels leadership/engineering as excluded overhead.
    assert _decimal(before["summary"]["unclassified"]) == Decimal("0.18")
    assert _decimal(before["by_role"]["engineer"]) == Decimal("0.9")
    assert _decimal(before["by_role"]["leader"]) == Decimal("0.18")
    assert _decimal(before["by_role"]["other operating"]) == Decimal("3.25")
    for dimension in ("role", "task", "root", "run", "provider", "model", "version"):
        assert sum((_decimal(amount) for amount in before[f"by_{dimension}"].values()), Decimal("0")) == Decimal("4.33")
        if dimension != "role":
            assert _decimal(before[f"by_{dimension}"]["unattributed"]) >= Decimal("3.25")

    runtime.clock.advance(1)
    runtime.ledger.add_expense(
        runtime.portfolio_id, expense_id="hosting-settlement",
        native_amount=Decimal("1.25"), native_currency="EUR",
        reporting_amount=Decimal("1.25"), reporting_currency="EUR",
        embedded=False, source="hosting invoice payment", settles="hosting-accrual",
    )
    settled = financial.costs(runtime)
    assert _decimal(settled["actual_spend"]) == Decimal("4.33")
    assert _decimal(settled["summary"]["actual_settled"]) == Decimal("1.25")
    assert len(settled["ledger_expenses"]) == 2
    for dimension in ("role", "task", "root", "run", "provider", "model", "version"):
        assert settled[f"by_{dimension}"] == before[f"by_{dimension}"]
    assert _decimal(financial.overview(runtime)["performance"]["net_economic_pnl"]) == Decimal("-2.15")
    runtime.portfolio_id = second
    assert _decimal(financial.costs(runtime)["actual_spend"]) == Decimal("4.33")
    assert _decimal(financial.overview(runtime)["performance"]["net_economic_pnl"]) == Decimal("-2")


@pytest.mark.parametrize("unexplained", [Decimal("0.25"), Decimal("-0.25")])
def test_invoice_discrepancy_is_visible_and_provisional_until_correcting_accrual(tmp_path, unexplained):
    runtime = _runtime(tmp_path)
    _receipt(runtime)
    before = runtime.budget.remaining("deployment")
    reconciliation = runtime.budget.reconcile_invoice(
        "deployment", "invoice-fixture", Decimal("0.9") + unexplained
    )
    projected = financial.costs(runtime)
    assert _decimal(projected["actual_spend"]) == Decimal("0.9")
    assert _decimal(projected["remaining_allowance"]) == before
    assert projected["provisional"] is True
    assert len(projected["invoice_adjustments"]) == 1
    discrepancy = projected["invoice_adjustments"][0]
    assert discrepancy["reconciliation_id"] == reconciliation.reconciliation_id
    assert _decimal(discrepancy["unexplained"]) == unexplained
    assert _decimal(discrepancy["recorded_total"]) == Decimal("0.9")
    assert _decimal(discrepancy["invoice_total"]) == Decimal("0.9") + unexplained
    overview = financial.overview(runtime)
    assert overview["provisional"] is True
    assert overview["performance"]["provisional"] is True
    assert overview["performance"]["net_economic_pnl"] is None


def test_receipt_accrual_keeps_original_reporting_valuation_when_current_fx_changes(tmp_path):
    runtime = _runtime(tmp_path)
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    runtime.clock.advance(60)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.5"),
        source="later hypothetical reference", kind="reference", stale=False,
    )
    projected = financial.costs(runtime)
    assert _decimal(projected["actual_spend"]) == Decimal("0.9")
    assert _decimal(projected["allocated_actual_spend"]) == Decimal("0.9")
    recorded = projected["receipts"][0]
    assert _decimal(recorded["native_cost"]) == Decimal("1")
    assert recorded["native_currency"] == "USD"
    assert _decimal(recorded["reporting_cost"]) == Decimal("0.9")
    basis = recorded["original_fx_basis"]
    assert basis["base"] == "USD"
    assert basis["quote"] == "EUR"
    assert _decimal(basis["rate"]) == Decimal("0.9")
    assert basis["source"] == "stored native and reporting accrual amounts"
    assert basis["rate_id"] is None
    assert basis["provenance_complete"] is False
    assert "source_id" not in basis
    assert _decimal(financial.overview(runtime)["performance"]["net_economic_pnl"]) == Decimal("-0.9")


def test_opening_and_closing_provenance_survives_private_dashboard_redaction(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    private_account = "private-owner-account-fixture-4826"
    execution = _execution(runtime, account_id=private_account)
    execution.save_observation(_quote(runtime, "opening-quote", size="1"))
    opening_decision = _decision(
        runtime, quantity=Quantity(amount="0.015", asset="BTC"),
        rationale="sk-fixture-private-rationale", invalidation="Bearer fixture-private-invalidation",
    )
    opening_intent = execution.authorize(runtime.portfolio_id, opening_decision)
    asyncio.run(execution.dispatch())
    runtime.clock.advance(1)
    execution.on_observation(_quote(runtime, "opening-fill", size="1"))
    assert execution.intent_state(opening_intent) == "FILLED"

    runtime.clock.advance(1)
    execution.save_observation(_quote(runtime, "closing-quote", size="1", bid="110", ask="111"))
    closing_decision = _decision(
        runtime, record_id="closing-decision", action="exit",
        system_version_id="closing-version", snapshot_id="obs:closing-quote",
        quantity=Quantity(amount="0.01", asset="BTC"),
        rationale="reduce position after target", invalidation="management decision fixture",
    )
    closing_intent = execution.authorize(runtime.portfolio_id, closing_decision)
    asyncio.run(execution.dispatch())
    runtime.clock.advance(1)
    execution.on_observation(_quote(runtime, "closing-fill", size="1", bid="110", ask="111"))
    assert execution.intent_state(closing_intent) == "FILLED"
    runtime.ledger.observe_mark(
        runtime.portfolio_id, "BTC", Decimal("110"), "USD", source="synthetic current mark"
    )

    receipt = _receipt(runtime)
    usage_row = runtime.database.execute(
        "SELECT usage_json FROM usage_receipts WHERE receipt_id = ?", (receipt,)
    ).fetchone()
    usage = json.loads(usage_row["usage_json"])
    usage["diagnostics"] = {
        "authorization": "Bearer nested-usage-private-token",
        "account_id": private_account,
        "raw_response": {"content": "private-verbatim-model-output"},
        "items": [{"note": "sk-fixture-nested-usage-secret", "unit_count": 3}],
    }
    runtime.database.execute(
        "UPDATE usage_receipts SET usage_json = ? WHERE receipt_id = ?", (json.dumps(usage), receipt)
    )
    runtime.database.execute(
        """INSERT INTO order_attempts (attempt_id, intent_id, kind, created_at, result_json)
        VALUES ('private-attempt-fixture', ?, 'lookup', ?, ?)""",
        (
            opening_intent, utc_iso(runtime.clock.now()),
            json.dumps({
                "broker": {
                    "account_id": private_account, "api_key": "fixture-nested-broker-key",
                    "notes": [{"message": "sk-fixture-nested-attempt-secret", "status": "known"}],
                },
                "response_json": {"verbatim": "private-verbatim-broker-response"},
                "status": "found",
            }),
        ),
    )

    positions = financial.positions(runtime)
    position = positions["positions"][0]
    assert _decimal(position["quantity"]) == Decimal("0.005")
    lot = position["lots"][0]
    assert lot["opening_decision_id"] == "sizing-decision"
    assert lot["opening_version_id"] == "trade-version"
    assert lot["disposals"][0]["closing_decision_id"] == "closing-decision"
    assert lot["disposals"][0]["closing_version_id"] == "closing-version"
    assert _decimal(lot["disposals"][0]["quantity"]) == Decimal("0.01")
    thesis = position["theses"][0]
    assert thesis["decision_id"] == "sizing-decision"
    assert thesis["snapshot_id"] == "obs:opening-quote"
    assert thesis["rationale"] == thesis["invalidation"] == "[redacted]"

    orders = financial.orders(runtime)
    opened = next(order for order in orders["orders"] if order["intent_id"] == opening_intent)
    assert opened["rationale"] == opened["invalidation"] == "[redacted]"
    assert "account_id" not in opened["fills"][0]
    attempt = next(attempt for attempt in opened["attempts"] if attempt["attempt_id"] == "private-attempt-fixture")
    assert attempt["result"]["status"] == "found"
    assert attempt["result"]["response_json"] == "[redacted]"
    assert attempt["result"]["broker"]["account_id"] == "[redacted]"
    assert attempt["result"]["broker"]["api_key"] == "[redacted]"
    assert attempt["result"]["broker"]["notes"][0] == {"message": "[redacted]", "status": "known"}

    costs = financial.costs(runtime)
    recorded = next(row for row in costs["receipts"] if row["receipt_id"] == receipt)
    diagnostics = recorded["usage"]["diagnostics"]
    assert diagnostics["authorization"] == diagnostics["account_id"] == diagnostics["raw_response"] == "[redacted]"
    assert diagnostics["items"][0] == {"note": "[redacted]", "unit_count": 3}
    serialized = json.dumps([financial.overview(runtime), positions, orders, costs])
    for private_value in (
        private_account, "sk-fixture-private-rationale", "fixture-private-invalidation",
        "nested-usage-private-token", "private-verbatim-model-output", "sk-fixture-nested-usage-secret",
        "fixture-nested-broker-key", "sk-fixture-nested-attempt-secret", "private-verbatim-broker-response",
    ):
        assert private_value not in serialized
    # Presentation redaction must preserve the retained private source evidence.
    stored = runtime.database.execute(
        "SELECT usage_json FROM usage_receipts WHERE receipt_id = ?", (receipt,)
    ).fetchone()
    assert "sk-fixture-nested-usage-secret" in stored["usage_json"]


@pytest.mark.parametrize("projection", [financial.costs, financial.positions, financial.orders])
@pytest.mark.parametrize("limit,offset", [(0, 0), (201, 0), (1, -1)])
def test_financial_pagination_rejects_invalid_bounds(tmp_path, projection, limit, offset):
    runtime = _runtime(tmp_path)
    with pytest.raises(ValueError, match="pagination"):
        projection(runtime, limit=limit, offset=offset)


def test_order_fills_are_scoped_to_the_portfolio_and_publish_only_declared_fields(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    execution = _execution(runtime)
    execution.save_observation(_quote(runtime, "opening-quote"))
    intent = execution.authorize(runtime.portfolio_id, _decision(runtime))
    asyncio.run(execution.dispatch())
    runtime.clock.advance(1)
    execution.on_observation(_quote(runtime, "own-fill"))
    own = runtime.database.execute(
        "SELECT * FROM fills WHERE intent_id = ? AND portfolio_id = ?", (intent, runtime.portfolio_id)
    ).fetchone()
    document = json.loads(own["document_json"])
    document["private_note"] = "private-owner-journal-fixture"
    document["unexpected_provider_metadata"] = {"detail": "private-provider-detail-fixture"}
    document["reference_mid"] = "99.5"
    runtime.database.execute(
        "UPDATE fills SET document_json = ? WHERE fill_id = ?", (json.dumps(document), own["fill_id"])
    )

    foreign_portfolio = runtime.ledger.create_portfolio(reporting_currency="EUR")
    foreign = {
        **document, "trade_id": "foreign-trade-fixture", "quantity": "99",
        "account_id": "private-foreign-account-fixture", "private_note": "private-foreign-note-fixture",
    }
    runtime.database.execute(
        """INSERT INTO fills
        (fill_id, venue, account_id, trade_id, portfolio_id, intent_id, document_json, created_at)
        VALUES ('foreign-fill-fixture', 'paper', ?, 'foreign-trade-fixture', ?, ?, ?, ?)""",
        (foreign["account_id"], foreign_portfolio, intent, json.dumps(foreign), utc_iso(runtime.clock.now())),
    )
    projected = financial.orders(runtime)
    order = projected["orders"][0]
    assert len(order["fills"]) == 1
    assert order["fills"][0]["fill_id"] == own["fill_id"]
    assert _decimal(order["filled_quantity"]) == Decimal("0.01")
    assert _decimal(order["remaining_quantity"]) == Decimal("0.005")
    assert _decimal(order["fills"][0]["execution_deviation"]) == Decimal("0.5")
    assert "private_note" not in order["fills"][0]
    assert "unexpected_provider_metadata" not in order["fills"][0]
    assert "account_id" not in order["fills"][0]
    for private in (
        "private-owner-journal-fixture", "private-provider-detail-fixture",
        "private-foreign-account-fixture", "private-foreign-note-fixture", "foreign-trade-fixture",
    ):
        assert private not in json.dumps(projected)


def test_future_fill_and_its_fee_source_are_excluded_from_an_earlier_projection(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.clock.advance(60)
    _fill(runtime, "future-trade-fixture")
    runtime.ledger.observe_mark(
        runtime.portfolio_id, "TEST", Decimal("44"), "EUR", source="future-mark-fixture"
    )
    assert _decimal(financial.overview(runtime)["performance"]["trading_fees"]["reporting_amount"]) == Decimal("0.4")
    runtime.clock.advance(-60)
    projected = financial.overview(runtime)
    assert _decimal(projected["equity"]) == Decimal("100")
    assert _decimal(projected["performance"]["trading_fees"]["reporting_amount"]) == 0
    assert projected["performance"]["trading_fees"]["native"] == []
    assert projected["performance"]["trading_fees"]["items"] == []
    assert "future-trade-fixture" not in json.dumps(projected)
    assert "future-mark-fixture" not in json.dumps(projected)
    assert financial.positions(runtime)["positions"] == []


def test_future_position_reservation_does_not_lock_current_cash(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.clock.advance(60)
    runtime.database.execute(
        """INSERT INTO position_reservations
        (reservation_id, portfolio_id, intent_id, asset, amount, state, created_at)
        VALUES ('future-hold', ?, 'future-intent', 'EUR', '10', 'held', ?)""",
        (runtime.portfolio_id, utc_iso(runtime.clock.now())),
    )
    runtime.clock.advance(-60)
    projected = financial.positions(runtime)
    assert projected["reservations"] == []
    assert projected["balances"][0]["owned"] == "100"
    assert projected["balances"][0]["reserved"] == "0"
    assert projected["balances"][0]["available"] == "100"


@pytest.mark.parametrize("policy_age", [None, 5])
def test_current_inventory_marks_expire_at_the_active_owner_policy_age(tmp_path, policy_age):
    runtime = _runtime(tmp_path)
    maximum_age = 30 if policy_age is None else policy_age
    if policy_age is not None:
        authority = seed_paper_authority(runtime.database, runtime.clock, runtime.portfolio_id)
        authority.install_policy(
            authority.active_policy().model_copy(
                update={"revision_id": "dashboard-age-policy", "maximum_quote_age_seconds": policy_age}
            ),
            role="owner",
        )
    _fill(runtime, "inventory-age-fixture")
    runtime.ledger.observe_mark(
        runtime.portfolio_id, "TEST", Decimal("44"), "EUR", source="age-limited-mark-fixture"
    )
    runtime.clock.advance(maximum_age)
    boundary = financial.overview(runtime)
    assert boundary["provisional"] is False
    assert _decimal(boundary["performance"]["trading_pnl"]) == Decimal("3.6")
    runtime.clock.advance(1)
    expired = financial.overview(runtime)
    assert _decimal(expired["equity"]) == Decimal("103.6")
    assert expired["provisional"] is True
    assert expired["performance"]["trading_pnl"] is None
    assert expired["performance"]["net_economic_pnl"] is None
    mark = expired["valuation"]["marks"][0]
    assert mark["age_seconds"] == maximum_age + 1
    assert mark["stale"] is True
    position = financial.positions(runtime)["positions"][0]
    assert position["provisional"] is True
    assert position["mark"]["stale"] is True


@pytest.mark.parametrize(
    "kind,override,maximum_age",
    [("reference", None, 7 * 86400), ("spot", None, 86400), ("reference", 5, 5), ("spot", 5, 5)],
)
def test_current_fx_expires_by_rate_kind_or_runtime_reporting_override(tmp_path, kind, override, maximum_age):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    if override is not None:
        runtime.reporting_fx_max_age_seconds = override
    runtime.clock.advance(1)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.91"),
        source="age-limited-fx-fixture", kind=kind, stale=False,
    )
    runtime.clock.advance(maximum_age)
    boundary = financial.overview(runtime)
    assert boundary["provisional"] is False
    assert _decimal(boundary["equity"]) == Decimal("9100")
    assert _decimal(boundary["allocated_capital"]["reporting_amount"]) == Decimal("9000")
    runtime.clock.advance(1)
    expired = financial.overview(runtime)
    assert _decimal(expired["equity"]) == Decimal("9100")
    assert expired["provisional"] is True
    assert expired["performance"]["trading_pnl"] is None
    assert expired["performance"]["net_economic_pnl"] is None
    basis = next(rate for rate in expired["valuation"]["fx"] if rate["base"] == "USD" and rate["quote"] == "EUR")
    assert basis["kind"] == kind
    assert basis["age_seconds"] == maximum_age + 1
    assert basis["stale"] is True


def test_historical_flows_and_receipt_fx_do_not_age_with_the_current_valuation(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    runtime.clock.advance(1)
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.91"),
        source="historical-flow-fx-fixture", kind="reference", stale=False,
    )
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("100"), "historical-owner-deposit")
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    runtime.clock.advance(8 * 86400)
    projected = financial.overview(runtime)
    assert projected["provisional"] is True
    assert projected["performance"]["net_economic_pnl"] is None
    assert _decimal(projected["allocated_capital"]["reporting_amount"]) == Decimal("9000")
    assert _decimal(projected["external_flows"]["net_reporting"]) == Decimal("91")
    for flow in projected["external_flows"]["items"]:
        assert flow["provisional"] is False
        assert flow["fx"]["stale"] is False
    costs = financial.costs(runtime)
    assert costs["provisional"] is False
    assert _decimal(costs["actual_spend"]) == Decimal("0.9")
    assert _decimal(costs["allocated_actual_spend"]) == Decimal("0.9")
    assert costs["receipts"][0]["original_fx_basis"]["rate"] == "0.9"
    assert costs["receipts"][0]["valuation"]["provisional"] is False
