"""T28: the paper broker fills through the shared fill engine and keeps its T05 guarantees (synthetic quotes)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers import paper as paper_module
from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, FillRecord, InstrumentRules, Observation, Quantity
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import Money
from trade_graph.kernel import fills as fill_engine
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, OKX_EEA, load_cost_profiles
from trade_graph.kernel.fills import PAPER_QUOTE_POLICY


def _rules() -> InstrumentRules:
    return InstrumentRules(venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
                           price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
                           min_notional="1", synthetic=True)


def _quote(clock, bid, ask, *, observation_id, size="1", volume=None, kind="quote") -> Observation:
    return Observation(observation_id=observation_id, venue="paper", symbol="BTC/USD", event_time_utc=clock.now(),
                       available_at_utc=clock.now(), bid=bid, ask=ask, bid_size=size, ask_size=size,
                       volume=volume, kind=kind, source="synthetic")


def _decision(clock, portfolio, record_id, **updates) -> Decision:
    payload = {
        "record_id": record_id, "created_at_utc": clock.now(), "run_id": "run", "task_id": "task",
        "root_task_id": "root", "portfolio_id": portfolio, "mode": "paper", "system_version_id": "v1",
        "trace_id": "trace", "action": "enter", "symbol": "BTC/USD", "quantity": Quantity(amount="0.01", asset="BTC"),
        "rationale": "synthetic fill-engine test", "invalidation": "below 90", "horizon_seconds": 3600,
        "strategy_id": "slow-trend", "snapshot_id": "snap", "mandate_revision": "1", "policy_revision": "1",
    }
    payload.update(updates)
    return Decision.model_validate(payload)


def _stack(tmp_path, name="fills.sqlite", **broker_options):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / name)
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock, **broker_options)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(_rules())
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    return clock, database, ledger, broker, execution, portfolio


def _buy(clock, execution, portfolio, record_id="buy"):
    execution.save_observation(_quote(clock, "99", "100", observation_id=f"{record_id}-seed"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio, record_id))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id=f"{record_id}-fill"))
    assert execution.intent_state(intent) == "FILLED"
    return intent


def _fills(database, intent_id) -> list[FillRecord]:
    return [FillRecord.model_validate_json(row["document_json"]) for row in database.execute(
        "SELECT document_json FROM fills WHERE intent_id = ?", (intent_id,)).fetchall()]


def _document(database, intent_id) -> dict:
    client = json.loads(database.execute("SELECT payload_json FROM order_intents WHERE intent_id = ?",
                                         (intent_id,)).fetchone()["payload_json"])["client_order_id"]
    return json.loads(database.execute("SELECT document_json FROM broker_orders WHERE client_order_id = ?",
                                       (client,)).fetchone()["document_json"])


def test_the_old_private_matcher_is_gone_and_the_broker_uses_the_shared_engine(tmp_path, monkeypatch) -> None:
    assert importlib.util.find_spec("trade_graph.adapters.brokers.matching") is None
    calls = []
    original = fill_engine.fill_order_on_observation

    def spy(*args, **kwargs):
        calls.append(args[0].order_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(paper_module, "fill_order_on_observation", spy)
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    _buy(clock, execution, portfolio)
    assert len(calls) == 1


def test_default_paper_fees_come_from_the_dated_kraken_starting_profile(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    profile = load_cost_profiles().get(KRAKEN_STARTING)
    assert broker.cost_profile == profile
    assert (broker.maker_rate, broker.taker_rate, broker.fee_reserve_rate) == (
        Decimal("0.004"), Decimal("0.008"), Decimal("0.008"))
    intent = _buy(clock, execution, portfolio)
    [fill] = _fills(database, intent)
    assert (fill.price, fill.fee_amount, fill.liquidity) == (Decimal("100"), Decimal("0.008"), "taker")
    [terms] = _document(database, intent)["fill_terms"]
    assert terms == {
        "trade_id": fill.trade_id, "reason": "market", "reference_price": "100",
        "cost_profile_ref": profile.ref, "cost_profile_sha256": profile.content_sha256,
        "fill_policy_ref": PAPER_QUOTE_POLICY.ref, "fill_policy_sha256": PAPER_QUOTE_POLICY.content_sha256,
    }


def test_paper_stop_gap_is_a_taker_exit_with_slippage_on_the_instrument_tick(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    _buy(clock, execution, portfolio)
    stop = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("95"), "snap")
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "96", "97", observation_id="above"))
    assert execution.intent_state(stop) == "OPEN"
    clock.advance(1)
    execution.on_observation(_quote(clock, "90", "91", observation_id="gap"))
    assert execution.intent_state(stop) == "FILLED"
    [fill] = _fills(database, stop)
    # Gapped bid 90, minus 0.1% slippage = 89.91, rounded down to the 0.1 tick: worse than the stop and the bid.
    assert (fill.price, fill.liquidity, fill.quantity) == (Decimal("89.9"), "taker", Decimal("0.01"))
    assert fill.fee_amount == Decimal("89.9") * Decimal("0.01") * Decimal("0.008")
    assert _document(database, stop)["fill_terms"][0]["reason"] == "stop_gap"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0")


def test_same_bar_and_touch_rules_hold_for_paper_limits(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    bar = _quote(clock, "99", "101", observation_id="bar-1", kind="bar", volume="5", size="5")
    execution.save_observation(bar)
    intent = execution.authorize(portfolio, _decision(
        clock, portfolio, "limit", snapshot_id="obs:bar-1", limit_price=Money(amount="100", currency="USD")))
    asyncio.run(execution.dispatch())
    execution.on_observation(bar)
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0")
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100.1", observation_id="above", volume="5"))
    assert execution.intent_state(intent) == "OPEN"
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="cross", volume="5"))
    assert execution.intent_state(intent) == "FILLED"
    [fill] = _fills(database, intent)
    assert (fill.price, fill.liquidity, fill.fee_amount) == (Decimal("100"), "maker", Decimal("0.004"))
    assert _document(database, intent)["fill_terms"][0]["reason"] == "limit_book_cross"


def test_a_profile_change_never_rewrites_earlier_paper_fills(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    first = _buy(clock, execution, portfolio, "first")
    ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="synthetic")
    before_fill = database.execute("SELECT document_json FROM fills WHERE intent_id = ?", (first,)).fetchone()[0]
    before_order = _document(database, first)
    okx = load_cost_profiles().get(OKX_EEA)
    switched = PaperBroker(database, clock, cost_profile=okx)
    execution = Execution(database, ledger, clock, switched)
    second = _buy(clock, execution, portfolio, "second")
    after_fill = database.execute("SELECT document_json FROM fills WHERE intent_id = ?", (first,)).fetchone()[0]
    assert after_fill == before_fill
    assert _document(database, first) == before_order
    [fill] = _fills(database, second)
    assert fill.fee_amount == Decimal("100") * Decimal("0.01") * Decimal("0.002")
    assert _document(database, second)["fill_terms"][0]["cost_profile_ref"] == okx.ref


def test_explicit_rates_stay_supported_and_are_labelled(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path, taker_rate=Decimal("0.005"))
    assert (broker.maker_rate, broker.taker_rate) == (Decimal("0.004"), Decimal("0.005"))
    assert broker.cost_profile.basis == "explicit_configuration"
    intent = _buy(clock, execution, portfolio)
    assert _fills(database, intent)[0].fee_amount == Decimal("0.005")
    assert _document(database, intent)["fill_terms"][0]["cost_profile_ref"] == broker.cost_profile.ref


def test_paper_broker_refuses_ambiguous_or_reservation_breaking_configuration(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "config.sqlite")
    with pytest.raises(ValidationFailure, match="either"):
        PaperBroker(database, clock, taker_rate=Decimal("0.005"),
                    cost_profile=load_cost_profiles().get(KRAKEN_STARTING))
    sloppy = PAPER_QUOTE_POLICY.model_copy(update={"entry_slippage_rate": Decimal("0.001")})
    with pytest.raises(ValidationFailure, match="entry slippage"):
        PaperBroker(database, clock, fill_policy=sloppy)


def test_orders_written_before_fill_terms_existed_stay_readable(tmp_path) -> None:
    clock, database, ledger, broker, execution, portfolio = _stack(tmp_path)
    intent = _buy(clock, execution, portfolio)
    document = _document(database, intent)
    document.pop("fill_terms")
    with database.immediate() as conn:
        conn.execute("UPDATE broker_orders SET document_json = ?", (json.dumps(document),))
    page = asyncio.run(broker.fills_since(None))
    assert [fill.intent_id for fill in page.fills] == [intent]
    assert asyncio.run(broker.open_orders()) == []
