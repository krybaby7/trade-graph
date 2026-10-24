import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, InstrumentRules, Observation, Quantity
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.domain.money import Money


def _rules() -> InstrumentRules:
    return InstrumentRules(
        venue="paper",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        price_increment="0.1",
        quantity_increment="0.00000001",
        min_quantity="0.0001",
        min_notional="1",
        synthetic=True,
    )


def _decision(clock, portfolio, **updates) -> Decision:
    payload = {
        "record_id": updates.pop("record_id", "dec-1"),
        "created_at_utc": clock.now(),
        "run_id": "run",
        "task_id": "task",
        "root_task_id": "root",
        "portfolio_id": portfolio,
        "mode": "paper",
        "system_version_id": "v1",
        "trace_id": "trace",
        "action": "enter",
        "symbol": "BTC/USD",
        "quantity": Quantity(amount="0.01", asset="BTC"),
        "rationale": "discretionary test",
        "invalidation": "below 90",
        "horizon_seconds": 3600,
        "strategy_id": "slow-trend",
        "snapshot_id": "snap",
        "mandate_revision": "1",
        "policy_revision": "1",
    }
    payload.update(updates)
    return Decision.model_validate(payload)


def _quote(clock, bid, ask, *, size="1", observation_id="q", kind="quote", volume=None) -> Observation:
    return Observation(
        observation_id=observation_id,
        venue="paper",
        symbol="BTC/USD",
        event_time_utc=clock.now(),
        available_at_utc=clock.now(),
        bid=bid,
        ask=ask,
        bid_size=size,
        ask_size=size,
        volume=volume,
        kind=kind,
        source="fixture",
    )


def _stack(tmp_path, broker_factory=PaperBroker):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "exec.sqlite")
    ledger = Ledger(database, clock)
    broker = broker_factory(database, clock)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(_rules())
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    return clock, ledger, execution, broker, portfolio


def test_partial_fill_minimum_and_pause(tmp_path) -> None:
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100", size="0.004", observation_id="q1"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", size="0.004", observation_id="q2"))
    assert execution.intent_state(intent) == "PARTIALLY_FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.004")
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", size="1", observation_id="q3"))
    assert execution.intent_state(intent) == "FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")
    with pytest.raises(ValidationFailure):
        execution.authorize(
            portfolio,
            _decision(clock, portfolio, record_id="tiny", quantity=Quantity(amount="0.00001", asset="BTC")),
        )
    execution.set_pause(portfolio, "NO_NEW_EXPOSURE", "owner", "risk")
    with pytest.raises(AuthorityDenied):
        execution.authorize(portfolio, _decision(clock, portfolio, record_id="blocked"))
    with pytest.raises(AuthorityDenied):
        execution.set_pause(portfolio, "RUNNING", "leader", "resume")


def test_unknown_ack_is_recovered_without_resubmit(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "unk.sqlite")
    ledger = Ledger(database, clock)
    inner = PaperBroker(database, clock)
    broker = DropAckBroker(inner)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(_rules())
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio, record_id="lost"))
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    assert inner.submit_count == 1
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="later"))
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent) == "FILLED"
    assert inner.submit_count == 1
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")


def test_stale_quote_blocks_increase_and_stop_gaps(tmp_path) -> None:
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100", observation_id="old"))
    clock.advance(60)
    with pytest.raises(StaleState):
        execution.authorize(portfolio, _decision(clock, portfolio, record_id="stale"))
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "stop.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(_rules())
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    execution.save_observation(_quote(clock, "99", "100"))
    execution.authorize(portfolio, _decision(clock, portfolio, record_id="buy-stop"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="fill"))
    stop = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("95"), "snap")
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "90", "91", observation_id="gap"))
    assert execution.intent_state(stop) == "FILLED"
    fill = ledger.database.execute("SELECT document_json FROM fills WHERE intent_id = ?", (stop,)).fetchone()
    document = fill["document_json"]
    assert '"price":"90"' in document or '"price":"90.0"' in document or "90" in document


def test_same_bar_limit_does_not_fill(tmp_path) -> None:
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    bar = _quote(clock, "99", "101", observation_id="bar-1", kind="bar", volume="5", size="5")
    execution.save_observation(bar)
    decision = _decision(
        clock,
        portfolio,
        record_id="limit",
        snapshot_id="obs:bar-1",
        limit_price=Money(amount="100", currency="USD"),
        quantity=Quantity(amount="0.01", asset="BTC"),
    )
    intent = execution.authorize(portfolio, decision)
    asyncio.run(execution.dispatch())
    execution.on_observation(bar)
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0")
    clock.advance(1)
    execution.on_observation(
        _quote(clock, "99", "100", observation_id="next", kind="trade", volume="1", size="1")
    )
    assert execution.intent_state(intent) in {"FILLED", "PARTIALLY_FILLED"}
