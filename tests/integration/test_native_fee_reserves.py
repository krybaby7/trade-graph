"""Paper-only auxiliary native holds protect shared funds across unknown outcomes."""

import asyncio
import json
import sqlite3
from decimal import Decimal, localcontext

import pytest
from tests.integration.test_execution import _decision, _quote, _stack

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.application.native_fee_reserves import (
    FeeAssetMaximum,
    PaperNativeFeeReserveController,
    PaperNativeFeeReservePlan,
)
from trade_graph.contracts.models import FillFeeRecord, FillRecord
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


def _seed_asset(clock, ledger, portfolio, *, quantity="1"):
    fill = FillRecord(
        venue="paper",
        account_id="paper",
        trade_id="synthetic-eth-seed",
        intent_id=None,
        symbol="ETH/USD",
        side="buy",
        quantity=quantity,
        price="10",
        fee_amount="0",
        fee_asset="USD",
        liquidity="taker",
        filled_at_utc=clock.now(),
        heuristic=True,
    )
    ledger.apply_fill(portfolio, fill, base_asset="ETH", quote_asset="USD")


def _setup(tmp_path, *, quantity="1", lost_ack=False):
    factory = (lambda database, clock: DropAckBroker(PaperBroker(database, clock))) if lost_ack else None
    clock, ledger, execution, broker, portfolio = _stack(tmp_path, **({"broker_factory": factory} if factory else {}))
    _seed_asset(clock, ledger, portfolio, quantity=quantity)
    ledger.observe_mark(portfolio, "ETH", Decimal("10"), "USD", source="synthetic-fee-asset")
    execution.save_observation(_quote(clock, "99", "100"))
    intent_id = execution.authorize(portfolio, _decision(clock, portfolio))
    return clock, ledger, execution, broker, portfolio, intent_id


def _plan(portfolio, intent_id, *components):
    return PaperNativeFeeReservePlan(
        portfolio_id=portfolio,
        intent_id=intent_id,
        source_ref="synthetic://protected-paper-fee-plan",
        components=components or (FeeAssetMaximum(asset="ETH", maximum_amount="0.6"),),
    )


def test_paper_secondary_hold_tracks_immutable_original_and_global_unreserved_assets(tmp_path):
    clock, ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    controller = PaperNativeFeeReserveController(execution)
    plan = _plan(portfolio, intent)
    identifiers = controller.reserve(plan)
    assert len(identifiers) == 1
    row = execution.database.execute("SELECT * FROM native_fee_reservations").fetchone()
    assert row["original_amount"] == row["current_amount"] == "0.6"
    assert row["plan_sha256"] == plan.sha256
    assert execution._reserved(portfolio, "ETH") == Decimal("0.6")
    other = execution.authorize(portfolio, _decision(clock, portfolio, record_id="second"))
    with pytest.raises(StaleState, match="unreserved"):
        controller.reserve(_plan(portfolio, other, FeeAssetMaximum(asset="ETH", maximum_amount="0.5")))
    assert execution.database.execute("SELECT count(*) FROM native_fee_reservations").fetchone()[0] == 1
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("9990")
    payload = json.loads(
        execution.database.execute(
            "SELECT payload_json FROM activity_events WHERE kind='paper_native_fee_reserves_created'",
        ).fetchone()[0]
    )
    assert payload["external_venue_bound_verified"] is False
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        execution.database.execute("UPDATE native_fee_reservations SET original_amount='100'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        execution.database.execute("DELETE FROM native_fee_reservations")
    execution.database.close()


def _fee_fill(clock, intent, *, quantity="0.004", amount="0.2", rebate="-0.1", trade="native-fee"):
    return FillRecord(
        venue="paper",
        account_id="paper",
        trade_id=trade,
        intent_id=intent,
        symbol="BTC/USD",
        side="buy",
        quantity=quantity,
        price="100",
        fee_amount="0",
        fee_asset="USD",
        liquidity="taker",
        filled_at_utc=clock.now(),
        heuristic=True,
        fee_components=(
            FillFeeRecord(
                asset="ETH",
                amount=amount,
                source_ref=trade + ":charge",
                effective_at_utc=clock.now(),
                identified_rate="10",
                rate_source_ref="synthetic-eth-usd",
            ),
            FillFeeRecord(
                asset="ETH",
                amount=rebate,
                source_ref=trade + ":credit",
                effective_at_utc=clock.now(),
                identified_rate="10",
                rate_source_ref="synthetic-eth-usd",
            ),
        ),
    )


def test_partial_fee_charges_consume_each_auxiliary_asset_once_and_rebates_do_not_refill(tmp_path):
    clock, ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    fill = _fee_fill(clock, intent)
    assert execution.record_fill(fill)
    row = execution.database.execute(
        "SELECT original_amount,current_amount,state FROM native_fee_reservations"
    ).fetchone()
    assert row[:] == ("0.6", "0.4", "held")
    assert execution._reserved(portfolio, "ETH") == Decimal("0.4")
    assert not execution.record_fill(fill)
    assert execution.database.execute("SELECT current_amount FROM native_fee_reservations").fetchone()[0] == "0.4"
    assert execution.owned_quantity(portfolio, "ETH") == Decimal("0.9")
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("9989.6")
    assert not execution._native_cost_limits_blocked()
    execution.database.close()


def test_secondary_fee_over_original_records_fact_and_sticky_asset_bound_incident(tmp_path):
    clock, ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    fill = _fee_fill(clock, intent, amount="0.7", rebate="-0.1")
    assert execution.record_fill(fill)
    assert execution.owned_quantity(portfolio, "ETH") == Decimal("0.4")
    assert execution.database.execute(
        "SELECT original_amount,current_amount,state FROM native_fee_reservations"
    ).fetchone()[:] == ("0.6", "0", "released")
    payload = json.loads(
        execution.database.execute(
            "SELECT payload_json FROM activity_events WHERE kind='native_fill_execution_limit_discrepancy'",
        ).fetchone()[0]
    )
    assert payload["reservation_excess"] == [{"asset": "ETH", "used": "0.7", "original": "0.6"}]
    assert payload["unreserved_fee_assets"] == []
    assert execution._native_cost_limits_blocked()
    assert ledger.journal_balanced(portfolio)
    execution.database.close()


def test_unknown_outcome_keeps_auxiliary_holds_then_owned_terminal_reconciliation_releases(tmp_path):
    clock, ledger, execution, broker, portfolio, intent = _setup(tmp_path, lost_ack=True)
    PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    assert asyncio.run(execution.dispatch()) == 0
    assert broker.inner.submit_count == 1
    assert execution.intent_state(intent) == "UNKNOWN"
    assert execution.database.execute("SELECT state,current_amount FROM native_fee_reservations").fetchone()[:] == (
        "held",
        "0.6",
    )
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="actual-paper-fill"))
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent) == "FILLED"
    assert execution.database.execute("SELECT state FROM native_fee_reservations").fetchone()[0] == "released"
    assert broker.inner.submit_count == 1
    assert ledger.journal_balanced(portfolio)
    execution.database.close()


def test_confirmed_cancel_releases_auxiliary_hold_and_allows_other_paper_intent(tmp_path):
    clock, _ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    controller = PaperNativeFeeReserveController(execution)
    controller.reserve(_plan(portfolio, intent))
    assert asyncio.run(execution.dispatch()) == 1
    asyncio.run(execution.cancel(intent))
    assert execution.intent_state(intent) == "CANCELLED"
    assert execution.database.execute("SELECT state FROM native_fee_reservations").fetchone()[0] == "released"
    assert execution._reserved(portfolio, "ETH") == 0
    other = execution.authorize(portfolio, _decision(clock, portfolio, record_id="after-cancel"))
    controller.reserve(_plan(portfolio, other))
    assert execution._reserved(portfolio, "ETH") == Decimal("0.6")
    execution.database.close()


@pytest.mark.parametrize(
    "problem", ["live_execution", "live_plan", "other_portfolio", "attempted", "same_primary", "duplicate"]
)
def test_paper_auxiliary_plan_never_waives_scope_or_replaces_original_authority(tmp_path, problem):
    _clock, ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    controller = PaperNativeFeeReserveController(execution)
    plan = _plan(portfolio, intent)
    if problem == "live_execution":
        execution.mode = "live"
        expected = AuthorityDenied
    elif problem == "live_plan":
        with pytest.raises(ValueError):
            PaperNativeFeeReservePlan.model_validate(dict(plan.model_dump(mode="json"), mode="live"))
        execution.database.close()
        return
    elif problem == "other_portfolio":
        plan = _plan(ledger.create_portfolio(reporting_currency="USD"), intent)
        expected = AuthorityDenied
    elif problem == "attempted":
        execution.database.execute("UPDATE order_intents SET state='UNKNOWN' WHERE intent_id=?", (intent,))
        expected = StaleState
    elif problem == "same_primary":
        plan = _plan(portfolio, intent, FeeAssetMaximum(asset="USD", maximum_amount="1"))
        expected = ValidationFailure
    else:
        controller.reserve(plan)
        expected = StaleState
    before = execution.database.execute("SELECT count(*) FROM native_fee_reservations").fetchone()[0]
    with pytest.raises(expected):
        controller.reserve(plan)
    assert execution.database.execute("SELECT count(*) FROM native_fee_reservations").fetchone()[0] == before
    execution.database.close()


@pytest.mark.parametrize(
    "amount", ["-1", "0", "NaN", "Infinity", 0.1, True, "1e-99999", "1e100", "0.12345678901234567890123456789"]
)
def test_fee_asset_maximum_rejects_unbounded_or_non_native_amounts(amount):
    with pytest.raises(ValueError):
        FeeAssetMaximum(asset="ETH", maximum_amount=amount)


@pytest.mark.parametrize("precision", [3, 50])
def test_paper_fee_holds_use_protected_context_independently_of_caller(tmp_path, precision):
    _clock, _ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    with localcontext() as context:
        context.prec = precision
        PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
        assert execution._reserved(portfolio, "ETH") == Decimal("0.6")
    execution.database.close()


def test_secondary_unknown_hold_survives_actual_database_restart_before_owned_terminal_release(tmp_path):
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.execution import Execution
    from trade_graph.application.ledger import Ledger

    clock, _ledger, execution, broker, portfolio, intent = _setup(tmp_path, lost_ack=True)
    PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    asyncio.run(execution.dispatch())
    assert broker.inner.submit_count == 1
    path = execution.database.path
    execution.database.close()
    database = Database(path)
    ledger = Ledger(database, clock)
    broker = DropAckBroker(PaperBroker(database, clock))
    execution = Execution(database, ledger, clock, broker)
    asyncio.run(execution.startup())
    assert database.execute("SELECT state,current_amount FROM native_fee_reservations").fetchone()[:] == ("held", "0.6")
    assert broker.inner.submit_count == 0
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="restart-terminal-fill"))
    asyncio.run(execution.reconcile())
    assert database.execute("SELECT state FROM native_fee_reservations").fetchone()[0] == "released"
    assert broker.inner.submit_count == 0
    database.close()


def test_auxiliary_position_projection_and_rebate_lot_keep_exact_fill_version_attribution(tmp_path):
    from types import SimpleNamespace

    from trade_graph.api import financial

    clock, ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    runtime = SimpleNamespace(database=execution.database, ledger=ledger, clock=clock, portfolio_id=portfolio)
    positions = financial.positions(runtime)
    eth = next(item for item in positions["positions"] if item["asset"] == "ETH")
    assert eth["reserved"] == "0.6" and eth["available"] == "0.4"
    assert execution.record_fill(_fee_fill(clock, intent))
    positions = financial.positions(runtime)
    eth = next(item for item in positions["positions"] if item["asset"] == "ETH")
    fee_lot = next(item for item in eth["lots"] if item["source_ref"] == "native-fee:fee:1")
    assert fee_lot["opening_decision_id"] == "dec-1"
    assert fee_lot["opening_version_id"] == "v1"
    assert eth["reserved"] == "0.4" and eth["available"] == "0.5"
    execution.database.close()


@pytest.mark.parametrize("problem", ["arbitrary_broker", "broker_subclass", "wrong_clock", "wrong_database"])
def test_auxiliary_hold_refuses_arbitrary_paper_label_or_wrong_broker_object_binding(tmp_path, problem):
    from tests.integration.test_execution_reconciliation_ordering import HistoryBroker

    from trade_graph.adapters.persistence.db import Database
    from trade_graph.domain.clock import FrozenClock

    clock, _ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    if problem == "arbitrary_broker":
        execution.broker = HistoryBroker()
    elif problem == "broker_subclass":

        class OtherBroker(PaperBroker):
            pass

        execution.broker = OtherBroker(execution.database, clock)
    elif problem == "wrong_clock":
        execution.broker = PaperBroker(execution.database, FrozenClock(clock.now()))
    else:
        execution.broker = PaperBroker(Database(tmp_path / "other.sqlite"), clock)
    with pytest.raises(AuthorityDenied, match="concrete local paper broker"):
        PaperNativeFeeReserveController(execution).reserve(_plan(portfolio, intent))
    assert execution.database.execute("SELECT count(*) FROM native_fee_reservations").fetchone()[0] == 0
    execution.database.close()


@pytest.mark.parametrize("current", ["-0.1", "1", "NaN"])
def test_corrupt_current_auxiliary_hold_cannot_increase_available_paper_assets(tmp_path, current):
    _clock, _ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    controller = PaperNativeFeeReserveController(execution)
    controller.reserve(_plan(portfolio, intent))
    execution.database.execute("UPDATE native_fee_reservations SET current_amount=?", (current,))
    with pytest.raises((StaleState, ValueError)):
        execution._reserved(portfolio, "ETH")
    execution.database.close()


def test_model_construct_cannot_bypass_positive_paper_reserve_amount_validation(tmp_path):
    _clock, _ledger, execution, _broker, portfolio, intent = _setup(tmp_path)
    fake = FeeAssetMaximum.model_construct(asset="ETH", maximum_amount=Decimal("-1"))
    plan = PaperNativeFeeReservePlan.model_construct(
        portfolio_id=portfolio,
        intent_id=intent,
        source_ref="synthetic",
        components=(fake,),
    )
    with pytest.raises(ValueError):
        PaperNativeFeeReserveController(execution).reserve(plan)
    assert execution.database.execute("SELECT count(*) FROM native_fee_reservations").fetchone()[0] == 0
    execution.database.close()
