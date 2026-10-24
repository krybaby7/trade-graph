"""Trader inference gets authoritative point-in-time financial and market inputs."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_artifact_consumers import _choice, _consumer_flow

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ModelRequest


def _context(flow, task_id):
    row = flow.db.execute("SELECT request_json FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()
    request = ModelRequest.model_validate_json(row[0])
    snapshot = flow.db.execute("SELECT payload_json FROM snapshots WHERE snapshot_id = ?", (request.run_id,)).fetchone()
    pinned = json.loads(snapshot[0])
    assert request.context["market"] == pinned["market"]
    assert request.context["portfolio"] == pinned["portfolio"]
    return request.context


@pytest.mark.parametrize("invisible", ["event_time", "available_at", "venue"])
def test_trader_request_excludes_future_and_other_venue_quotes(tmp_path, invisible):
    flow = _consumer_flow(tmp_path, activate=False)
    execution = flow.office.execution
    visible = execution.latest_observation("BTC/USD", execution.now(), "paper")
    updates = {"observation_id": "unavailable", "bid": Decimal("1000"), "ask": Decimal("1001")}
    if invisible == "event_time":
        updates["event_time_utc"] = flow.clock.now() + timedelta(seconds=60)
    elif invisible == "available_at":
        updates["available_at_utc"] = flow.clock.now() + timedelta(seconds=60)
    else:
        updates["venue"] = "other"
    execution.save_observation(visible.model_copy(update=updates))
    task_id = flow.add_turn()
    assert flow.run() == 1 and flow.row(task_id)["status"] == "SUCCEEDED"
    context = _context(flow, task_id)
    assert context["market"]["BTC/USD"]["observation"]["observation_id"] == "visible-quote"
    assert context["market"]["BTC/USD"]["features"]["mid"] == "99.5"
    assert context["market"]["BTC/USD"]["fresh"] is True
    flow.db.close()


def test_inference_snapshot_keeps_cash_coherent_during_concurrent_deposit(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path, activate=False)
    writer = Database(flow.db.path)
    initial = flow.office.execution.ledger.books(flow.pid).cash_amount("USD")
    latest = flow.office.execution.latest_observation
    changed = False

    def deposit_after_quote(*args, **kwargs):
        nonlocal changed
        result = latest(*args, **kwargs)
        if not changed:
            changed = True
            Ledger(writer, flow.clock).deposit(flow.pid, "USD", Decimal("17"), "concurrent")
        return result

    monkeypatch.setattr(flow.office.execution, "latest_observation", deposit_after_quote)
    try:
        task_id = flow.add_turn()
        assert flow.run() == 1 and flow.row(task_id)["status"] == "SUCCEEDED"
        context = _context(flow, task_id)
        assert Decimal(context["portfolio"]["cash"]["USD"]) == initial
        assert flow.office.execution.ledger.books(flow.pid).cash_amount("USD") == initial + 17
    finally:
        writer.close()
        flow.db.close()


def test_next_inference_discloses_held_order_reservations_and_stale_quote(tmp_path):
    flow = _consumer_flow(tmp_path, activate=False)
    flow.gateway.scripted.outputs["trader"] = _choice("enter")
    opening = flow.add_turn()
    assert flow.run() == 1 and flow.row(opening)["status"] == "SUCCEEDED"
    flow.clock.advance(31)
    flow.gateway.scripted.outputs["trader"] = _choice("hold")
    task_id = flow.add_turn()
    assert flow.run() == 1 and flow.row(task_id)["status"] == "SUCCEEDED"
    context = _context(flow, task_id)
    assert context["market"]["BTC/USD"]["fresh"] is False
    assert len(context["portfolio"]["open_orders"]) == 1
    assert Decimal(context["portfolio"]["reserved"]["USD"]) > 0
    assert context["portfolio"]["open_orders"][0]["state"] == "SUBMISSION_PENDING"
    assert flow.office.execution.broker.submit_count == 0
    flow.db.close()
