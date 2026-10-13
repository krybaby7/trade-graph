"""A refused order is a recorded, visible outcome; it neither fails the Trader's turn nor pauses the system."""

import asyncio
import json
from decimal import Decimal

import pytest
from tests.integration.test_artifact_consumers import _choice, _consumer_flow
from tests.integration.test_execution import _rules
from tests.integration.test_owned_subscription_service import subscription_service
from tests.integration.test_research_trader import _stack
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.contracts.models import ModelResult, Observation
from trade_graph.domain.errors import ValidationFailure

MINIMUM = "below venue minimum; size was not increased"
HOLD = {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
        "invalidation": "New evidence.", "experiment": False, "evidence_ids": [], "no_action_reason": "No signal."}


def _quote(flow):
    flow.office.execution.save_observation(Observation(
        observation_id="refusal-quote", venue="paper", symbol="BTC/USD", event_time_utc=flow.clock.now(),
        available_at_utc=flow.clock.now(), bid="99", ask="100", bid_size="1", ask_size="1",
        kind="quote", source="synthetic-fixture"))


def _reply(quantity="0.00001"):
    return {"action": "enter", "symbol": "BTC/USD", "quantity": quantity, "strategy_id": "slow-trend-pullback",
            "rationale": "Synthetic small entry inside the mandate.", "invalidation": "The visible trend fails.",
            "experiment": False, "evidence_ids": [], "no_action_reason": None}


def _fund(flow):
    flow.office.execution.ledger.deposit(flow.pid, "USD", Decimal("10000"), "opening-funds")
    flow.office.execution.register_instrument(_rules())
    _quote(flow)


def _count(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


def _assert_recorded_refusal(db, task_id, output):
    assert output["action"] == "enter" and output["intent_id"] is None
    assert output["refusal"]["code"] == "validation" and output["refusal"]["reason"] == MINIMUM
    decisions = db.execute("SELECT * FROM decisions WHERE task_id = ?", (task_id,)).fetchall()
    assert len(decisions) == 1
    assert decisions[0]["action"] == "enter" and decisions[0]["decision_id"] == output["decision_id"]
    for table in ("order_intents", "position_reservations", "outbox"):
        assert _count(db, f"SELECT COUNT(*) FROM {table}") == 0, table
    events = db.execute("SELECT payload_json FROM activity_events WHERE kind = 'order_refused'").fetchall()
    assert len(events) == 1
    payload = json.loads(events[0][0])
    assert payload["decision_id"] == output["decision_id"] and payload["task_id"] == task_id
    assert payload["symbol"] == "BTC/USD" and payload["quantity"] == "0.00001"
    assert payload["side"] == "buy" and payload["order_type"] == "market" and payload["limit_price"] is None
    assert payload["code"] == "validation" and payload["error"] == "ValidationFailure"
    assert payload["reason"] == MINIMUM
    assert _count(db, "SELECT COUNT(*) FROM activity_events WHERE kind = 'role_failed'") == 0
    assert _count(db, "SELECT COUNT(*) FROM secretary_reports WHERE kind IN ('failed', 'incident')") == 0
    report = json.loads(db.execute("SELECT document_json FROM secretary_reports WHERE report_id = ?",
                                   (output["report_id"],)).fetchone()[0])
    assert report["kind"] == "decision" and report["summary"].endswith(f"\nOrder not placed: {MINIMUM}")
    return payload


def _assert_next_turn_sees(context, output, payload):
    refusals = context["portfolio"]["recent_order_refusals"]
    assert refusals == [{key: payload[key] for key in ("decision_id", "refused_at", "action", "symbol", "quantity",
                                                       "order_type", "limit_price", "code", "reason")}]
    assert refusals[0]["decision_id"] == output["decision_id"] and refusals[0]["reason"] == MINIMUM


def test_refused_buy_in_subscription_service_is_recorded_without_failing_or_pausing(tmp_path, monkeypatch):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    _fund(flow)
    cli.outputs["trader"] = _reply()
    refused = flow.add("trader")
    later = []

    async def scenario():
        await service.tick(wait_roles=True)
        cli.outputs["trader"] = HOLD
        later.append(flow.add("trader"))
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    outcome = (flow.row(refused)["status"], runtime.execution.profile(flow.pid))
    assert outcome == ("SUCCEEDED", "RUNNING"), flow.row(refused)["output_json"]
    output = json.loads(flow.row(refused)["output_json"])
    payload = _assert_recorded_refusal(flow.db, refused, output)
    assert flow.row(later[0])["status"] == "SUCCEEDED", flow.row(later[0])["output_json"]
    assert len(cli.requests) == 2
    _assert_next_turn_sees(cli.requests[-1].context, output, payload)


def test_refused_buy_through_api_trader_handler_is_recorded_and_shown_next_turn(tmp_path):
    flow = RuntimeFlow(tmp_path)
    _fund(flow)
    flow.transport.outputs["TraderReply"] = _reply()
    refused = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(refused)["status"] == "SUCCEEDED", flow.row(refused)["output_json"]
    output = json.loads(flow.row(refused)["output_json"])
    payload = _assert_recorded_refusal(flow.db, refused, output)
    flow.transport.outputs["TraderReply"] = HOLD
    later = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(later)["status"] == "SUCCEEDED", flow.row(later)["output_json"]
    _assert_next_turn_sees(json.loads(flow.transport.calls[-1]["body"]["input"][1]["content"]), output, payload)


@pytest.mark.parametrize(("action", "quantity", "advance", "code", "reason"), [
    ("enter", "0.00001", 0, "validation", MINIMUM),
    ("enter", "60", 0, "authority_denied", "single asset exposure"),
    ("enter", "0.01", 31, "stale_state", "stale or missing quote"),
    ("exit", "0.02", 0, "validation", "insufficient available balance"),
])
def test_trader_act_records_each_authorize_refusal_class(tmp_path, action, quantity, advance, code, reason):
    clock, database, _execution, _research, trader, portfolio = _stack(tmp_path)
    clock.advance(advance)
    payload = {**_reply(quantity), "action": action}
    turn = trader.act(portfolio, ModelResult(ok=True, payload=payload), snapshot_id="snap", task_id="t-1")
    assert turn.kind == "refused" and turn.action == action
    assert turn.intent_id is None and turn.decision_id
    assert turn.refusal["code"] == code and turn.refusal["reason"] == reason
    assert turn.refusal["side"] == ("buy" if action == "enter" else "sell")
    assert turn.refusal["order_type"] == "market" and turn.refusal["limit_price"] is None
    assert turn.refusal["quantity"] == quantity and turn.refusal["decision_id"] == turn.decision_id
    assert turn.refusal["snapshot_id"] == "snap" and turn.refusal["task_id"] == "t-1"
    rows = database.execute("SELECT action, decision_id FROM decisions").fetchall()
    assert [tuple(row) for row in rows] == [(action, turn.decision_id)]
    events = database.execute("SELECT payload_json FROM activity_events WHERE kind = 'order_refused'").fetchall()
    assert [json.loads(row[0]) for row in events] == [turn.refusal]
    assert _count(database, "SELECT COUNT(*) FROM order_intents") == 0
    assert _count(database, "SELECT COUNT(*) FROM position_reservations") == 0


def test_contract_errors_still_raise_and_record_no_refusal(tmp_path):
    _clock, database, _execution, _research, trader, portfolio = _stack(tmp_path)
    with pytest.raises(ValidationFailure, match="strategy template is unknown"):
        trader.act(portfolio, ModelResult(ok=True, payload={**_reply(), "strategy_id": "invented"}),
                   snapshot_id="snap", template_documents={})
    assert _count(database, "SELECT COUNT(*) FROM decisions") == 0
    assert _count(database, "SELECT COUNT(*) FROM activity_events WHERE kind = 'order_refused'") == 0


def test_malformed_trader_reply_fails_only_that_turn(tmp_path, monkeypatch):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    _fund(flow)
    cli.outputs["trader"] = {"action": "hold"}
    task_id = flow.add("trader")

    async def scenario():
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    # A malformed answer is a failed turn, not a refusal and not a system pause (owner, 2026-10-13).
    assert flow.row(task_id)["status"] == "FAILED"
    assert json.loads(flow.row(task_id)["output_json"])["failure_scope"] == "turn"
    assert runtime.execution.profile(flow.pid) == "RUNNING"
    assert _count(flow.db, "SELECT COUNT(*) FROM activity_events WHERE kind = 'order_refused'") == 0


def test_refusal_is_a_failed_version_trial_sample_not_a_healthy_one(tmp_path):
    flow = _consumer_flow(tmp_path)
    flow.gateway.scripted.outputs["trader"] = {**_choice("enter"), "quantity": "0.00001"}
    task_id = flow.add_turn()
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED", flow.row(task_id)["output_json"]
    assert json.loads(flow.row(task_id)["output_json"])["refusal"]["reason"] == MINIMUM
    observations = flow.db.execute("SELECT * FROM version_observations").fetchall()
    assert len(observations) == 1 and observations[0]["observation_id"] == task_id
    document = json.loads(observations[0]["document_json"])
    assert document["ok"] is False and document["reason"] == MINIMUM
