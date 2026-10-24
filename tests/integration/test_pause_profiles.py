"""Pause profiles reach an achieved state only when that state is verified."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    CancelResult,
    Decision,
    InstrumentRules,
    Observation,
    OrderLookupResult,
    Quantity,
    SubmitResult,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied


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


def _quote(clock, bid, ask, *, size="1", observation_id="q") -> Observation:
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
        kind="quote",
        source="fixture",
    )


def _stack(tmp_path, broker_factory=PaperBroker):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "pause.sqlite")
    ledger = Ledger(database, clock)
    broker = broker_factory(database, clock)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(_rules())
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    return clock, execution, broker, portfolio


def _fill_entry(clock, execution, portfolio) -> str:
    execution.save_observation(_quote(clock, "99", "100", observation_id="entry-quote"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio, record_id="entry"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="entry-fill"))
    assert execution.intent_state(intent) == "FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")
    return intent


def test_each_pause_profile_records_its_verified_state(tmp_path) -> None:
    clock, execution, _broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100", observation_id="q0"))
    resting = execution.authorize(portfolio, _decision(clock, portfolio, record_id="resting"))
    asyncio.run(execution.dispatch())
    execution.set_pause(portfolio, "PAUSE_DECISIONS", "owner", "stand aside")
    assert asyncio.run(execution.advance_pause(portfolio)) == "decisions-paused"
    assert execution.intent_state(resting) == "OPEN"
    with pytest.raises(AuthorityDenied):
        execution.authorize(portfolio, _decision(clock, portfolio, record_id="paused-entry"))

    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "manage")
    assert asyncio.run(execution.advance_pause(portfolio)) == "managing"
    assert execution.intent_state(resting) == "OPEN"
    with pytest.raises(AuthorityDenied):
        execution.authorize(portfolio, _decision(clock, portfolio, record_id="managed-entry"))
    with pytest.raises(AuthorityDenied):
        execution.set_pause(portfolio, "RUNNING", "leader", "resume")


def test_no_new_exposure_cancels_buys_and_keeps_protection(tmp_path) -> None:
    clock, execution, _broker, portfolio = _stack(tmp_path)
    _fill_entry(clock, execution, portfolio)
    execution.ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="fixture")
    protection = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("50"), "snap")
    asyncio.run(execution.dispatch())
    execution.save_observation(_quote(clock, "99", "100", observation_id="q-again"))
    extra = execution.authorize(portfolio, _decision(clock, portfolio, record_id="extra"))
    asyncio.run(execution.dispatch())
    execution.set_pause(portfolio, "NO_NEW_EXPOSURE", "owner", "cap")
    assert asyncio.run(execution.advance_pause(portfolio)) == "increases-cleared"
    assert execution.intent_state(extra) == "CANCELLED"
    assert execution.intent_state(protection) == "OPEN"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")


def test_cancel_all_clears_orders_without_declaring_flat(tmp_path) -> None:
    clock, execution, _broker, portfolio = _stack(tmp_path)
    _fill_entry(clock, execution, portfolio)
    protection = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("50"), "snap")
    asyncio.run(execution.dispatch())
    execution.set_pause(portfolio, "CANCEL_ALL", "owner", "clear orders")
    assert asyncio.run(execution.advance_pause(portfolio)) == "orders-cleared"
    assert execution.intent_state(protection) == "CANCELLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")
    assert execution.pause(portfolio)["profile"] == "CANCEL_ALL"


def test_flatten_is_not_complete_until_the_exit_fills(tmp_path) -> None:
    clock, execution, broker, portfolio = _stack(tmp_path)
    _fill_entry(clock, execution, portfolio)
    protection = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("50"), "snap")
    asyncio.run(execution.dispatch())
    execution.save_observation(_quote(clock, "100", "100.1", observation_id="flatten-quote"))
    execution.set_pause(portfolio, "FLATTEN", "owner", "close")
    assert asyncio.run(execution.advance_pause(portfolio)) == "flattening"
    assert execution.intent_state(protection) == "CANCELLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")
    assert asyncio.run(execution.advance_pause(portfolio)) == "flattening"
    assert broker.submit_count == 3
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="flatten-fill"))
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0")
    assert asyncio.run(execution.advance_pause(portfolio)) == "flat-verified"
    execution.set_pause(portfolio, "STOPPED", "owner", "done")
    assert asyncio.run(execution.advance_pause(portfolio)) == "stopped"


def test_stopped_stays_blocked_while_inventory_remains(tmp_path) -> None:
    clock, execution, _broker, portfolio = _stack(tmp_path)
    _fill_entry(clock, execution, portfolio)
    execution.set_pause(portfolio, "STOPPED", "owner", "stop")
    assert asyncio.run(execution.advance_pause(portfolio)) == "blocked-until-flat"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")


def test_unknown_cancel_is_not_treated_as_cleared(tmp_path) -> None:
    class StuckBroker:
        def __init__(self, database, clock) -> None:
            self.inner = PaperBroker(database, clock)
            self.submit_count = 0

        async def capabilities(self):
            return await self.inner.capabilities()

        async def instruments(self):
            return await self.inner.instruments()

        async def balances(self):
            return await self.inner.balances()

        async def open_orders(self):
            return await self.inner.open_orders()

        async def fills_since(self, cursor):
            return await self.inner.fills_since(cursor)

        async def submit(self, intent):
            self.submit_count += 1
            return SubmitResult(status="uncertain", error="timeout_uncertain")

        async def cancel(self, request):
            return CancelResult(status="uncertain", error="timeout_uncertain")

        async def order_status(self, key):
            return OrderLookupResult(status="unknown", filled_quantity=Decimal("0"))

        def match(self, observation):
            return []

    clock, execution, broker, portfolio = _stack(tmp_path, StuckBroker)
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    execution.set_pause(portfolio, "CANCEL_ALL", "owner", "unknown")
    assert asyncio.run(execution.advance_pause(portfolio)) == "cancelling-orders"
    assert execution.intent_state(intent) != "CANCELLED"
    assert broker.submit_count == 1

    database = Database(tmp_path / "drop.sqlite")
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
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
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    execution.set_pause(portfolio, "CANCEL_ALL", "owner", "lost ack")
    assert asyncio.run(execution.advance_pause(portfolio)) == "orders-cleared"
    assert execution.intent_state(intent) == "CANCELLED"
    assert inner.submit_count == 1
