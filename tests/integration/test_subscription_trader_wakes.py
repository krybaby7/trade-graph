"""The subscription Trader answers a wake with day plans (W6-D, D223).

A real event engine makes the wake; the real protected subscription Trader (synthetic CLI, no model call) is handed
the wake and the practice-range strategy versions, answers ``TraderWakeReply`` under ``wake_outcome``, and the engine
applies the answer: the check-in is recorded and the day plan goes through ``DayPlans.install`` and its checks.
Nothing here places an order by itself.
"""

from __future__ import annotations

import json
from decimal import Decimal

from tests.day_plan_support import plan
from tests.integration.test_subscription_departments import flow

from trade_graph.adapters.models.subscription import SubscriptionConfig
from trade_graph.application.events import EventEngine, TaskSettings
from trade_graph.application.subscription_runtime import ROLE_CONTEXT_KEYS, SubscriptionRuntimeConfig
from trade_graph.application.trader_workflow import TraderReply
from trade_graph.contracts.wakes import TraderWakeReply

# An owner graph that passes a wake reply through (the reference graph in scripts/verify_protected_image.py does);
# the older test graph edits a Trader reply's rationale, which a wake reply does not have.
WAKE_GRAPH = '''
def graph(context):
    if context["operation"] == "invoke_model":
        return {"node":"invoke_model", "guidance":"Confined " + context["role"] + " reasoning."}
    payload = context["result"]
    if "rationale" in payload:
        payload["rationale"] = "Confined completion: " + payload["rationale"]
    return {"node":"apply_role_result", "payload":payload}
'''


def _engine(setup) -> EventEngine:
    version = setup.versions.load_active(setup.pid)["artifact_hash"]
    return EventEngine(setup.office.execution, setup.office.scheduler, departments=("trader",), daily_cycle=False,
                       task_settings=lambda _pid, _role: TaskSettings(allocated_spend=Decimal("0"), max_attempts=1,
                                                                      expected_version=version))


def _wake_answer() -> dict:
    return {"day_plans": [plan().model_dump(mode="json")],
            "next_check_in": {"at_utc": "2026-01-01T06:00:00Z", "note": "after the next London open"},
            "summary": "Rest one SOL bid under the market with its stop."}


def test_the_subscription_trader_answers_a_wake_with_day_plans_and_the_engine_applies_them(tmp_path) -> None:
    setup, _protected, cli = flow(tmp_path, source=WAKE_GRAPH)
    engine = _engine(setup)
    engine.request_wake(setup.pid, "trader", "Synthetic: SOL looks interesting.", requested_by="leader")
    report = engine.step([setup.pid])
    assert report.created == 1
    cli.outputs["trader"] = _wake_answer()
    decisions = setup.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert setup.run("trader") == 1
    (request,) = [item for item in cli.requests if item.role == "trader"]
    assert request.schema_name == "TraderWakeReply"
    assert request.output_schema == TraderWakeReply.model_json_schema()
    assert request.context["wake"]["purpose"] == "event" and request.context["wake"]["reasons"] == ["wake_request"]
    assert request.context["wake"]["triggers_total"] == 1 and "practice_range" in request.context
    assert "day_plans" in request.instructions and "practice_range" in request.instructions
    task = setup.db.execute("SELECT * FROM tasks WHERE role = 'trader'").fetchone()
    assert task["status"] == "SUCCEEDED", task["output_json"]
    output = json.loads(task["output_json"])
    assert output["wake_outcome"]["day_plans"][0]["symbol"] == "SOL/USD" and output["report_id"]
    assert setup.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == decisions  # no single-choice turn
    assert setup.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    engine.step([setup.pid])
    (wake,) = engine.wakes(setup.pid)
    assert wake.status == "completed" and wake.outcome.received
    assert wake.outcome.summary == "Rest one SOL bid under the market with its stop."
    (applied,) = wake.outcome.plans
    # This synthetic account has no recorded money limits, so DayPlans.install refuses, says why, and nothing rests.
    assert applied.symbol == "SOL/USD" and applied.status == "refused" and applied.reason
    assert engine.next_check_ins(setup.pid)["trader"]["basis"] == "named"


def test_a_task_that_is_not_a_wake_keeps_the_single_choice_reply(tmp_path) -> None:
    setup, _protected, cli = flow(tmp_path, source=WAKE_GRAPH)
    identity = setup.add("trader")
    assert setup.run("trader") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    (request,) = [item for item in cli.requests if item.role == "trader"]
    assert request.schema_name == "TraderReply" and request.output_schema == TraderReply.model_json_schema()
    assert "wake" not in request.context and "practice_range" not in request.context


def test_the_wake_keys_are_in_the_protected_allowlist_and_an_owner_profile_without_them_drops_them(tmp_path) -> None:
    assert {"wake", "practice_range"} <= ROLE_CONTEXT_KEYS["trader"]
    for role in ("research", "learning", "optimisation", "leader", "engineer"):
        assert "practice_range" not in ROLE_CONTEXT_KEYS[role]
    older = {role: sorted(keys - {"wake", "practice_range"}) for role, keys in ROLE_CONTEXT_KEYS.items()}
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="scripted", enabled=True, allowed_context_keys=older))
    assert "wake" not in config.subscription.allowed_context_keys["trader"]  # what step L3 adds to the profile


def test_an_owner_graph_that_rewrites_the_rationale_sets_the_wake_turn_aside_without_effects(tmp_path) -> None:
    """Step L3 checks the owner's graph.py: one that edits a Trader reply's rationale cannot handle a wake reply."""
    setup, _protected, cli = flow(tmp_path)  # the older test graph
    engine = _engine(setup)
    engine.request_wake(setup.pid, "trader", "Synthetic note.", requested_by="leader")
    engine.step([setup.pid])
    cli.outputs["trader"] = _wake_answer()
    assert setup.run("trader") == 1
    task = setup.db.execute("SELECT * FROM tasks WHERE role = 'trader'").fetchone()
    assert task["status"] == "FAILED"
    assert setup.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    engine.step([setup.pid])
    (wake,) = engine.wakes(setup.pid)
    assert wake.status == "failed" and wake.outcome is None
