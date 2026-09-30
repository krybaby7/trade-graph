"""Authority comes from persisted owner policy and mandate revisions."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import (
    AuthorityRecord,
    paper_expiry,
    paper_mandate,
    paper_owner_policy,
    seed_paper_authority,
)
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    BrokerCapabilities,
    Decision,
    FillRecord,
    InstrumentRules,
    Observation,
    Quantity,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord
from trade_graph.domain.money import Money


def _decision(clock, portfolio, **updates) -> Decision:
    payload = {
        "record_id": "dec-1",
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
        "rationale": "authority binding",
        "invalidation": "stale mandate",
        "horizon_seconds": 3600,
        "strategy_id": "slow-trend",
        "snapshot_id": "snap",
        "mandate_revision": "1",
        "policy_revision": "1",
    }
    payload.update(updates)
    return Decision.model_validate(payload)


def _stack(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "auth.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(
        InstrumentRules(
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
    )
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    execution.save_observation(
        Observation(
            observation_id="q",
            venue="paper",
            symbol="BTC/USD",
            event_time_utc=clock.now(),
            available_at_utc=clock.now(),
            bid="99",
            ask="100",
            bid_size="100",
            ask_size="100",
            kind="quote",
            source="fixture",
        )
    )
    return clock, database, ledger, execution, broker, portfolio


def test_missing_persisted_mandate_blocks_authorization(tmp_path) -> None:
    clock, _, _, execution, _, portfolio = _stack(tmp_path)
    with pytest.raises(AuthorityDenied):
        execution.authorize(portfolio, _decision(clock, portfolio))


def test_policy_revisions_are_immutable_and_owner_only(tmp_path) -> None:
    clock, database, _, _, _, _ = _stack(tmp_path)
    record = AuthorityRecord(database, clock)
    policy = paper_owner_policy()
    with pytest.raises(AuthorityDenied):
        record.install_policy(policy, role="leader")
    record.install_policy(policy, role="owner")
    record.install_policy(policy, role="owner")
    changed = policy.model_copy(update={"monthly_operating": Money(amount="9", currency="EUR")})
    with pytest.raises(DuplicateRecord):
        record.install_policy(changed, role="owner")
    withdrawals = paper_owner_policy(revision_id="2")
    withdrawals = withdrawals.model_copy(update={"withdrawals_allowed": True})
    with pytest.raises(AuthorityDenied):
        record.install_policy(withdrawals, role="owner")


def test_mandate_must_stay_inside_persisted_owner_envelope(tmp_path) -> None:
    clock, database, _, _, _, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    wider = paper_mandate(portfolio, revision=2, mandate_id="wide", gross="0.95")
    with pytest.raises(AuthorityDenied):
        record.install_mandate(wider, role="leader")
    with pytest.raises(AuthorityDenied):
        record.install_mandate(wider, role="trader")


def test_authorize_uses_active_mandate_not_a_caller_cap(tmp_path) -> None:
    clock, database, _, execution, _, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    record.install_mandate(
        paper_mandate(portfolio, revision=2, mandate_id="tight", gross="0.10", asset="0.10"),
        role="owner",
    )
    with pytest.raises(AuthorityDenied):
        execution.authorize(
            portfolio,
            _decision(clock, portfolio, mandate_revision="1", quantity=Quantity(amount="30", asset="BTC")),
        )
    with pytest.raises(AuthorityDenied):
        execution.authorize(
            portfolio,
            _decision(
                clock,
                portfolio,
                record_id="tight",
                mandate_revision="2",
                quantity=Quantity(amount="30", asset="BTC"),
            ),
        )
    execution.authorize(
        portfolio,
        _decision(
            clock,
            portfolio,
            record_id="small",
            mandate_revision="2",
            quantity=Quantity(amount="1", asset="BTC"),
        ),
    )


def test_expired_mandate_blocks_entry_and_allows_exit(tmp_path) -> None:
    clock, database, ledger, execution, _, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    record.install_mandate(
        paper_mandate(
            portfolio,
            revision=2,
            mandate_id="short",
            expires_at=paper_expiry(clock, seconds=5),
        ),
        role="owner",
    )
    clock.advance(6)
    execution.save_observation(
        Observation(
            observation_id="fresh",
            venue="paper",
            symbol="BTC/USD",
            event_time_utc=clock.now(),
            available_at_utc=clock.now(),
            bid="99",
            ask="100",
            bid_size="10",
            ask_size="10",
            kind="quote",
            source="fixture",
        )
    )
    with pytest.raises(AuthorityDenied):
        execution.authorize(portfolio, _decision(clock, portfolio, mandate_revision="2"))
    ledger.apply_fill(
        portfolio,
        FillRecord(
            venue="paper",
            account_id="paper",
            trade_id="seed-fill",
            intent_id=None,
            symbol="BTC/USD",
            side="buy",
            quantity="0.02",
            price="100",
            fee_amount="0",
            fee_asset="USD",
            liquidity="taker",
            filled_at_utc=clock.now(),
        ),
        base_asset="BTC",
        quote_asset="USD",
    )
    execution.authorize(
        portfolio,
            _decision(
                clock,
                portfolio,
                record_id="exit",
                action="exit",
                mandate_revision="2",
                quantity=Quantity(amount="0.02", asset="BTC"),
            ),
    )


def test_tightened_mandate_rejects_unsent_increase_before_submit(tmp_path) -> None:
    clock, database, _, execution, broker, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    intent = execution.authorize(
        portfolio,
        _decision(clock, portfolio, quantity=Quantity(amount="30", asset="BTC")),
    )
    record.install_mandate(
        paper_mandate(portfolio, revision=2, mandate_id="tighter", gross="0.10", asset="0.10"),
        role="leader",
    )
    asyncio.run(execution.dispatch())
    assert broker.submit_count == 0
    assert execution.intent_state(intent) == "REJECTED"
    assert execution._reserved(portfolio, "USD") == 0


def test_non_discretionary_strategy_must_be_named(tmp_path) -> None:
    clock, database, _, execution, _, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    record.install_mandate(
        paper_mandate(
            portfolio,
            revision=2,
            mandate_id="closed",
            discretionary_experiment=False,
            strategy_ids=["slow-trend"],
        ),
        role="owner",
    )
    with pytest.raises(AuthorityDenied):
        execution.authorize(
            portfolio,
            _decision(clock, portfolio, mandate_revision="2", strategy_id="unlisted"),
        )


def test_dispatch_refuses_withdrawal_capability_and_binding_mismatch(tmp_path) -> None:
    clock, database, ledger, _, broker, portfolio = _stack(tmp_path)

    class WithdrawBroker(PaperBroker):
        async def capabilities(self) -> BrokerCapabilities:
            caps = await super().capabilities()
            return caps.model_copy(update={"withdrawals": True})

    withdraw = WithdrawBroker(database, clock)
    execution = Execution(database, ledger, clock, withdraw)
    seed_paper_authority(database, clock, portfolio)
    execution.authorize(portfolio, _decision(clock, portfolio, record_id="withdraw-cap"))
    with pytest.raises(AuthorityDenied):
        asyncio.run(execution.dispatch())
    assert withdraw.submit_count == 0
    assert broker.submit_count == 0

    class MismatchedBroker(PaperBroker):
        async def capabilities(self) -> BrokerCapabilities:
            caps = await super().capabilities()
            return caps.model_copy(update={"venue": "kraken", "mode": "live"})

    rebound = Execution(database, ledger, clock, MismatchedBroker(database, clock))
    rebound.authorize(portfolio, _decision(clock, portfolio, record_id="mismatch"))
    with pytest.raises(AuthorityDenied):
        asyncio.run(rebound.dispatch())


def test_duplicate_mandate_revision_cannot_change(tmp_path) -> None:
    clock, database, _, _, _, portfolio = _stack(tmp_path)
    record = seed_paper_authority(database, clock, portfolio)
    changed = paper_mandate(portfolio, revision=1, gross="0.20")
    with pytest.raises(DuplicateRecord):
        record.install_mandate(changed, role="owner")
    expired = paper_mandate(
        portfolio,
        revision=3,
        mandate_id="already-expired",
        expires_at=clock.now() - timedelta(seconds=1),
    )
    with pytest.raises(Exception):
        record.install_mandate(expired, role="owner")
