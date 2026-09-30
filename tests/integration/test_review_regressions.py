"""Reproductions from the 2026-09-30 review; no credentials or network."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    CancelResult,
    Decision,
    FillPage,
    FillRecord,
    InstrumentRules,
    ModelUsage,
    Observation,
    OrderLookupResult,
    PriceCard,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState, UncertainExternal, ValidationFailure
from trade_graph.kernel.books import Books, Mark, apply_fill, deposit, mark_equity
from trade_graph.kernel.pricing import usage_cost, worst_case_cost

D = Decimal
NOW = datetime(2026, 9, 30, tzinfo=UTC)


def fill(**updates):
    return FillRecord.model_validate({
        "venue": "paper", "account_id": "paper", "trade_id": "f", "intent_id": None, "symbol": "BTC/USD",
        "side": "buy", "quantity": "1", "price": "100", "fee_amount": "0", "fee_asset": "USD",
        "liquidity": "taker", "filled_at_utc": NOW, "heuristic": False, **updates,
    })


def decision(portfolio, **updates):
    return Decision.model_validate({
        "record_id": "d", "created_at_utc": NOW, "run_id": "r", "task_id": "t", "root_task_id": "t",
        "portfolio_id": portfolio, "mode": "paper", "system_version_id": "v", "trace_id": "t",
        "action": "enter", "symbol": "BTC/USD", "quantity": {"amount": "1", "asset": "BTC"},
        "rationale": "fixture", "invalidation": "fixture", "horizon_seconds": 60, "strategy_id": "s",
        "snapshot_id": "snap", "mandate_revision": "1", "policy_revision": "1", **updates,
    })


@pytest.fixture
def stack(tmp_path):
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "review.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, broker)
    for symbol in ("BTC", "ETH"):
        execution.register_instrument(InstrumentRules(
            venue="paper", symbol=f"{symbol}/USD", base_asset=symbol, quote_asset="USD",
            price_increment="0.1", quantity_increment="0.001", min_quantity="0.001",
            min_notional="1", synthetic=True,
        ))
        execution.save_observation(Observation(
            observation_id=symbol, venue="paper", symbol=f"{symbol}/USD", event_time_utc=NOW,
            available_at_utc=NOW, bid="100", ask="100", bid_size="100", ask_size="100",
            kind="quote", source="fixture",
        ))
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", D("10000"), "seed")
    ledger.observe_fx(base="USD", quote="EUR", rate=D("0.9"), source="fixture", kind="spot", stale=False)
    seed_paper_authority(database, clock, portfolio)
    yield clock, database, ledger, execution, broker, portfolio
    database.close()


def authorize(stack, **updates):
    _, _, _, execution, _, portfolio = stack
    return execution.authorize(portfolio, decision(portfolio, **updates))


def test_fifo_disposals_conserve_total_proceeds():
    books = Books()
    deposit(books, "USD", D("1000"), "0", "seed")
    for n, qty, price in [(1, "0.4", "40"), (2, "0.6", "50")]:
        apply_fill(books, fill(quantity=qty, price=price, trade_id=str(n)), base_asset="BTC", quote_asset="USD",
                   lot_id=str(n), at=str(n))
    apply_fill(books, fill(side="sell", quantity="0.5", price="60", trade_id="sell"),
               base_asset="BTC", quote_asset="USD", lot_id="unused", at="3")
    disposals = [item for lot in books.lots for item in lot.disposals]
    assert sum(item.proceeds for item in disposals) == D("30")
    assert sum(item.realized for item in disposals) == D("9")


def test_third_asset_sell_fee_is_charged_once():
    books = Books()
    deposit(books, "USD", D("1000"), "0", "seed")
    for asset, price in [("BTC", "100"), ("FEE", "10")]:
        apply_fill(books, fill(symbol=f"{asset}/USD", price=price), base_asset=asset, quote_asset="USD",
                   lot_id=asset, at="1")
    apply_fill(books, fill(side="sell", price="120", fee_asset="FEE", fee_amount="0.1",
                          fee_identified_rate="10"), base_asset="BTC", quote_asset="USD", lot_id="x", at="2")
    assert books.cash_amount("USD") == D("1010")
    view = mark_equity(books, reporting="USD", marks=[Mark("FEE", D("10"), "USD", "2", False, "fixture")],
                       rates=[], at="2")
    assert view.equity == D("1019")  # gain 20, fee 1, not fee 2


def test_fill_projection_failure_rolls_back_the_entire_ledger(stack):
    _, database, ledger, execution, _, portfolio = stack
    intent = authorize(stack)
    record = fill(intent_id=intent)
    before = ledger.books(portfolio).cash_amount("USD")
    database.execute("CREATE TRIGGER fail_fill BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        execution.record_fill(record)
    assert ledger.books(portfolio).cash_amount("USD") == before
    assert execution.owned_quantity(portfolio, "BTC") == 0
    database.execute("DROP TRIGGER fail_fill")
    assert execution.record_fill(record)
    assert not execution.record_fill(record)
    assert execution.owned_quantity(portfolio, "BTC") == 1
    assert ledger.journal_balanced(portfolio)


def test_replay_repairs_legacy_orphan_fill_projection(stack):
    _, database, ledger, execution, _, portfolio = stack
    record = fill(intent_id=authorize(stack))
    ledger.apply_fill(portfolio, record, base_asset="BTC", quote_asset="USD")
    assert execution.record_fill(record)
    assert execution.owned_quantity(portfolio, "BTC") == 1
    assert database.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_default_usd_capital_eur_reporting_uses_matching_units(stack):
    assert authorize(stack, quantity={"amount": "48", "asset": "BTC"})  # 48%, not 53.33%


def test_pending_buys_count_towards_single_asset_cap(stack):
    authorize(stack, record_id="a", quantity={"amount": "30", "asset": "BTC"})
    with pytest.raises(AuthorityDenied):
        authorize(stack, record_id="b", quantity={"amount": "30", "asset": "BTC"})


def test_gross_cap_includes_other_assets(stack):
    _, _, ledger, execution, _, portfolio = stack
    ledger.apply_fill(portfolio, fill(symbol="ETH/USD", quantity="40"), base_asset="ETH", quote_asset="USD")
    ledger.observe_mark(portfolio, "ETH", D("100"), "USD", source="fixture")
    with pytest.raises(AuthorityDenied):
        authorize(stack, quantity={"amount": "43", "asset": "BTC"})
    assert execution.owned_quantity(portfolio, "ETH") == 40


def test_concurrent_authorizations_cannot_overbook_cap(stack):
    barrier = Barrier(2)

    def attempt(n):
        barrier.wait(timeout=5)
        try:
            authorize(stack, record_id=str(n), quantity={"amount": "30", "asset": "BTC"})
            return True
        except AuthorityDenied:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1


@pytest.mark.parametrize("updates", [
    {"quantity": {"amount": "1", "asset": "ETH"}},
    {"limit_price": {"amount": "100", "currency": "EUR"}},
    {"mode": "live"}, {"action": "resize"}, {"action": "adjust_order"},
])
def test_mismatched_or_ambiguous_order_contract_is_rejected(stack, updates):
    with pytest.raises((ValidationFailure, AuthorityDenied)):
        authorize(stack, **updates)


def test_owner_halt_cannot_be_laundered_through_leader_pause(stack):
    _, _, _, execution, _, portfolio = stack
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "owner halt")
    with pytest.raises(AuthorityDenied):
        execution.set_pause(portfolio, "NO_NEW_EXPOSURE", "leader", "replace owner state")
    assert execution.pause(portfolio)["originator"] == "owner"


def test_queued_entry_does_not_dispatch_after_pause(stack):
    _, _, _, execution, broker, portfolio = stack
    intent = authorize(stack)
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "pause before dispatch")
    asyncio.run(execution.dispatch())
    assert broker.submit_count == 0
    assert execution.intent_state(intent) == "SUBMISSION_PENDING"


def test_delayed_old_quote_does_not_become_fresh_on_arrival(stack):
    clock, database, _, execution, _, _ = stack
    database.execute("DELETE FROM observations")
    execution.save_observation(Observation(
        observation_id="old", venue="paper", symbol="BTC/USD", event_time_utc=NOW-timedelta(hours=1),
        available_at_utc=clock.now(), bid="100", ask="100", kind="quote", source="delayed",
    ))
    with pytest.raises(StaleState):
        authorize(stack)


def test_quote_from_other_venue_does_not_authorize_order(stack):
    _, database, _, execution, _, _ = stack
    database.execute("DELETE FROM observations")
    execution.save_observation(Observation(
        observation_id="other", venue="other", symbol="BTC/USD", event_time_utc=NOW,
        available_at_utc=NOW, bid="100", ask="100", kind="quote", source="fixture",
    ))
    with pytest.raises(StaleState):
        authorize(stack)


def test_protective_orders_cannot_double_reserve_inventory(stack):
    _, _, ledger, execution, _, portfolio = stack
    ledger.apply_fill(portfolio, fill(), base_asset="BTC", quote_asset="USD")
    execution.place_protection(portfolio, "BTC/USD", D("1"), D("90"), "snap")
    with pytest.raises(ValidationFailure):
        execution.place_protection(portfolio, "BTC/USD", D("1"), D("90"), "snap")


def card(**updates):
    return PriceCard.model_validate({
        "price_card_id": "c", "provider": "openai", "model": "fixture", "endpoint": "fixture",
        "currency": "EUR", "input_per_million": "1", "output_per_million": "2",
        "cache_read_per_million": "0", "cache_write_per_million": "3", "search_per_call": "0",
        "effective_at": "2026-09-30", "verified_at": "2026-09-30", "source_id": "fixture",
        "tier": "fixture", "context_band": "fixture", **updates,
    })


@pytest.fixture
def budget(stack):
    clock, database, _, _, _, _ = stack
    gateway = BudgetGateway(database, clock)
    gateway.configure(deployment_id="d", currency="EUR", total=D("100"), period=D("100"),
                      priority_reserve=D("0"), daily=D("100"), root=D("100"), roles={"trader": D("100")})
    gateway.seed_card(card())
    return gateway


def reserve(budget, **updates):
    return budget.reserve(**{
        "deployment_id": "d", "role": "trader", "task_id": "t", "root_task_id": "t", "price_card_id": "c",
        "max_input": 1000, "max_output": 100, "max_tools": 0, "fx_rate": D("0.9"), "fx_buffer": D("1"),
        "priority": False, "synthetic": False, "purpose": "fixture", **updates,
    })


def test_zero_read_rate_and_distinct_write_rate():
    usage = ModelUsage(uncached_input_tokens=10, cache_read_tokens=20, cache_write_tokens=30, billed_output_tokens=5)
    assert usage_cost(card(), usage) == D("0.00011")
    assert worst_case_cost(card(), 100, 0, 0) == D("0.0003")


def test_missing_cache_write_price_stays_unresolved():
    with pytest.raises(ValueError):
        usage_cost(card(cache_write_per_million=None), ModelUsage(uncached_input_tokens=0,
                                                                billed_output_tokens=0, cache_write_tokens=1))


@pytest.mark.parametrize("field", ["uncached_input_tokens", "billed_output_tokens", "cache_read_tokens",
                                   "cache_write_tokens", "reasoning_tokens", "tool_units"])
def test_negative_usage_is_rejected(field):
    with pytest.raises(ValueError):
        ModelUsage.model_validate({"uncached_input_tokens": 1, "billed_output_tokens": 1, field: -1})


def test_negative_price_is_rejected():
    with pytest.raises(ValueError):
        card(input_per_million="-1")


def test_eur_reservation_does_not_apply_usd_fx(budget):
    reserve(budget)
    assert budget.remaining("d") == D("100") - worst_case_cost(card(), 1000, 100, 0)


@pytest.mark.parametrize("updates", [{"fx_rate": D("0")}, {"fx_rate": D("-1")}, {"fx_buffer": D("0.5")}])
def test_invalid_reservation_fx_is_rejected(budget, updates):
    with pytest.raises(ValueError):
        reserve(budget, **updates)


def test_price_card_revision_cannot_be_repriced(budget):
    reserve(budget)
    with pytest.raises(ValueError):
        budget.seed_card(card(input_per_million="0"))


def test_receipt_commit_is_idempotent_and_terminal(budget):
    reservation = reserve(budget)
    usage = ModelUsage(uncached_input_tokens=10, billed_output_tokens=1)
    receipt = budget.commit(reservation, usage, provider="openai", model="fixture", fx_rate=D("0.9"))
    assert budget.commit(reservation, usage, provider="openai", model="fixture", fx_rate=D("0.9")) == receipt
    budget.mark_uncertain(reservation)
    assert budget.database.execute("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                                   (reservation,)).fetchone()[0] == "COMMITTED"
    assert budget.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1


def test_shared_cost_cannot_be_allocated_twice(budget):
    receipt = budget.conservative_charge(reserve(budget))
    budget.allocate(receipt, {"p1": D("1")})
    budget.allocate(receipt, {"p1": D("1")})  # idempotent retry
    with pytest.raises(ValueError):
        budget.allocate(receipt, {"p2": D("1")})
    with pytest.raises(ValueError):
        budget.allocate(receipt, {"a": D("2"), "b": D("-1")})


def test_openai_null_error_and_cache_write_usage():
    parsed = OpenAIAdapter().parse({
        "id": "fixture", "error": None, "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
        "usage": {"input_tokens": 100, "output_tokens": 1,
                  "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 20}},
    })
    assert parsed.ok
    assert parsed.usage.uncached_input_tokens == 50
    assert parsed.usage.cache_write_tokens == 20


def test_anthropic_input_is_already_uncached():
    parsed = AnthropicAdapter().parse({
        "id": "fixture", "stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}],
        "usage": {"input_tokens": 8, "output_tokens": 1, "cache_read_input_tokens": 2,
                  "cache_creation_input_tokens": 1},
    })
    assert parsed.usage.uncached_input_tokens == 8
    assert parsed.usage.cache_read_tokens == 2
    assert parsed.usage.cache_write_tokens == 1


@pytest.mark.parametrize("adapter,payload", [
    (OpenAIAdapter(), {"output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}]}),
    (AnthropicAdapter(), {"content": [{"type": "text", "text": "[]"}]}),
])
def test_nonobject_model_output_is_a_validation_failure(adapter, payload):
    assert adapter.parse(payload).failure == "validation"


def test_sell_limit_rounding_preserves_minimum_price(stack):
    _, _, ledger, execution, _, portfolio = stack
    ledger.apply_fill(portfolio, fill(), base_asset="BTC", quote_asset="USD")
    intent = authorize(stack, action="exit", limit_price={"amount": "100.05", "currency": "USD"})
    assert execution._intent_model(intent).limit_price == D("100.1")


@pytest.mark.parametrize("changes", [{"account_id": "wrong"}, {"venue": "wrong"},
                                      {"symbol": "ETH/USD"}, {"side": "sell"}, {"quantity": "2"}])
def test_fill_must_match_intent_and_remaining_quantity(stack, changes):
    _, _, _, execution, _, _ = stack
    intent = authorize(stack)
    with pytest.raises(ValidationFailure):
        execution.record_fill(fill(intent_id=intent, **changes))


def test_cancel_ack_without_all_fills_does_not_release_reservation(stack):
    _, _, _, execution, broker, portfolio = stack
    intent = authorize(stack)
    asyncio.run(execution.dispatch())

    async def status(_):
        return OrderLookupResult(status="cancelled", filled_quantity="0.5")

    async def pages(_):
        return FillPage(fills=[])

    broker.order_status = status
    broker.fills_since = pages
    asyncio.run(execution.cancel(intent))
    assert execution.intent_state(intent) == "UNKNOWN"
    assert execution._reserved(portfolio, "USD") > 0


def test_uncertain_cancellation_cannot_submit_replacement(stack):
    _, database, _, execution, broker, portfolio = stack
    intent = authorize(stack)
    asyncio.run(execution.dispatch())

    async def cancel(_):
        return CancelResult(status="uncertain")

    broker.cancel = cancel
    with pytest.raises(UncertainExternal):
        asyncio.run(execution.replace(intent, decision(portfolio, record_id="replacement")))
    assert database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 1


def test_reconciliation_reads_all_fill_pages_before_releasing(stack):
    _, _, _, execution, broker, portfolio = stack
    intent = authorize(stack)
    asyncio.run(execution.dispatch())
    seen = []

    async def status(_):
        return OrderLookupResult(status="filled", filled_quantity="1")

    async def pages(cursor):
        seen.append(cursor)
        if cursor is None:
            return FillPage(fills=[fill(intent_id=intent, quantity="0.4", trade_id="f1")], next_cursor="next")
        return FillPage(fills=[fill(intent_id=intent, quantity="0.6", trade_id="f2")])

    broker.order_status = status
    broker.fills_since = pages
    asyncio.run(execution.reconcile())
    assert seen == [None, "next"]
    assert execution.owned_quantity(portfolio, "BTC") == 1
    assert execution.intent_state(intent) == "FILLED"
    assert execution._reserved(portfolio, "USD") == 0
