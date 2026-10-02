"""Late submit replies preserve independently booked fills across restart."""

import asyncio
import json

import pytest
from tests.integration.test_execution import _decision, _quote, _stack

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import SubmitResult
from trade_graph.domain.errors import UncertainExternal


class DelayedReplyBroker(PaperBroker):
    def __init__(self, database, clock, outcome):
        super().__init__(database, clock)
        self.outcome = outcome
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def submit(self, intent):
        accepted = await super().submit(intent)
        self.started.set()
        await self.finish.wait()
        if self.outcome == "timeout":
            raise UncertainExternal("synthetic delayed acknowledgement")
        if self.outcome == "conflicting_identity":
            return accepted.model_copy(update={"venue_order_id": "different-native-order"})
        if self.outcome == "acknowledged":
            return accepted
        return SubmitResult(
            status=self.outcome, error="timeout_uncertain" if self.outcome == "uncertain" else "rejected",
        )


@pytest.mark.parametrize("size, expected", [("0.004", "PARTIALLY_FILLED"), ("1", "FILLED")])
@pytest.mark.parametrize("outcome", ["acknowledged", "uncertain", "rejected", "timeout"])
def test_late_submission_cannot_regress_fill_or_release_remaining_reservation(tmp_path, size, expected, outcome):
    clock, ledger, execution, broker, portfolio = _stack(
        tmp_path, lambda database, clock: DelayedReplyBroker(database, clock, outcome),
    )
    database = execution.database
    execution.save_observation(_quote(clock, "99", "100", observation_id="seed"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))

    async def timeline():
        submitting = asyncio.create_task(execution.dispatch())
        await asyncio.wait_for(broker.started.wait(), 2)
        clock.advance(1)
        execution.on_observation(_quote(clock, "99", "100", size=size, observation_id="during-submit"))
        await execution.reconcile()
        assert execution.intent_state(intent) == expected
        reserved = execution._reserved(portfolio, "USD")
        cash = ledger.books(portfolio).cash_amount("USD")
        broker.finish.set()
        await asyncio.wait_for(submitting, 2)
        assert execution.intent_state(intent) == expected
        assert execution._reserved(portfolio, "USD") == reserved
        assert ledger.books(portfolio).cash_amount("USD") == cash
        assert execution._payload(intent)["late_submission"]["state"] in {"OPEN", "UNKNOWN", "REJECTED"}
        return reserved, cash

    reserved, cash = asyncio.run(timeline())
    assert database.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    path = database.path
    database.close()
    reopened = Database(path)
    try:
        restored_ledger = Ledger(reopened, clock)
        restored_broker = PaperBroker(reopened, clock)
        restored = Execution(reopened, restored_ledger, clock, restored_broker)
        asyncio.run(restored.startup())
        assert restored.intent_state(intent) == expected
        assert restored._reserved(portfolio, "USD") == reserved
        assert restored_ledger.books(portfolio).cash_amount("USD") == cash
        assert reopened.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert restored_broker.submit_count == 0
    finally:
        reopened.close()


def test_conflicting_late_native_identity_preserves_fill_and_blocks_increases(tmp_path):
    clock, _, execution, broker, portfolio = _stack(
        tmp_path, lambda database, clock: DelayedReplyBroker(database, clock, "conflicting_identity"),
    )
    execution.save_observation(_quote(clock, "99", "100", observation_id="seed"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))

    async def timeline():
        submitting = asyncio.create_task(execution.dispatch())
        await asyncio.wait_for(broker.started.wait(), 2)
        clock.advance(1)
        broker.match(_quote(clock, "99", "100", observation_id="filled"))
        await execution.reconcile()
        identity = execution._payload(intent)["venue_order_id"]
        broker.finish.set()
        await asyncio.wait_for(submitting, 2)
        assert execution.intent_state(intent) == "FILLED"
        assert execution._payload(intent)["venue_order_id"] == identity
        assert execution._reconciliation_blocked()
        await execution.reconcile()
        assert execution._reconciliation_blocked()

    try:
        asyncio.run(timeline())
    finally:
        execution.database.close()


def test_terminal_cancel_retains_reconciled_identity_after_lost_ack(tmp_path):
    clock, _, execution, _, portfolio = _stack(
        tmp_path, lambda database, clock: DropAckBroker(PaperBroker(database, clock)),
    )
    execution.save_observation(_quote(clock, "99", "100", observation_id="seed"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    assert execution._payload(intent).get("venue_order_id") is None
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", size="0.004", observation_id="partial"))
    asyncio.run(execution.cancel(intent))
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent) == "CANCELLED"
    native_id = execution._payload(intent)["venue_order_id"]
    path = execution.database.path
    execution.database.close()
    reopened = Database(path)
    try:
        row = reopened.execute("SELECT payload_json FROM order_intents WHERE intent_id=?", (intent,)).fetchone()
        assert json.loads(row["payload_json"])["venue_order_id"] == native_id
    finally:
        reopened.close()
