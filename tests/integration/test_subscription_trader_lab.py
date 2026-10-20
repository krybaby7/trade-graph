"""The subscription Trader (the Trading desk) sees its strategy lab and asks it on a wake (W11-B, C11; D290).

The real protected subscription Trader (synthetic CLI, no model call) is handed the ``lab`` key on a wake, answers
``TraderWakeReply`` with ``lab_requests``, and the event engine queues them with the rest of the answer. The ``lab``
key is in the protected allowlist for the Trader only; an owner profile that does not list it yet drops it (step L3
adds it). Nothing here places an order or runs a replay.
"""

from __future__ import annotations

import json

from tests.integration.test_subscription_departments import flow
from tests.integration.test_subscription_trader_wakes import WAKE_GRAPH, _engine
from tests.strategy_lab_support import register

from trade_graph.adapters.models.subscription import SubscriptionConfig
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.application.subscription_runtime import ROLE_CONTEXT_KEYS, SubscriptionRuntimeConfig


def test_a_wake_turn_is_handed_the_lab_and_its_lab_requests_are_queued(tmp_path) -> None:
    setup, _protected, cli = flow(tmp_path, source=WAKE_GRAPH)
    engine = _engine(setup)
    engine.request_wake(setup.pid, "trader", "Synthetic: write a better strategy.", requested_by="leader")
    engine.step([setup.pid])
    cli.outputs["trader"] = {"summary": "Register a version for the lab.",
                             "lab_requests": [register().model_dump(mode="json")]}
    assert setup.run("trader") == 1
    (request,) = [item for item in cli.requests if item.role == "trader"]
    lab = request.context["lab"]
    assert lab["available"] is True and lab["account_cost_profile"] == "kraken-spot-starting-tier"
    assert lab["results"] == [] and lab["queue"]["queued"] == 0
    assert "lab_requests" in request.instructions and "lab_requests" in lab["instruction"]
    assert "lab_requests" in request.output_schema["properties"]
    task = setup.db.execute("SELECT output_json FROM tasks WHERE role = 'trader'").fetchone()
    report = setup.db.execute("SELECT * FROM secretary_reports WHERE report_id = ?",
                              (json.loads(task["output_json"])["report_id"],)).fetchone()
    assert "Lab requests: 1" in json.loads(report["document_json"])["summary"]
    engine.step([setup.pid])
    (wake,) = engine.wakes(setup.pid)
    assert wake.status == "completed" and [item.status for item in wake.outcome.lab] == ["queued"]
    queue = LabQueue(setup.db, setup.clock)
    assert queue.counts(setup.pid) == {"queued": 1, "running": 0}
    assert setup.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_the_lab_key_is_the_traders_only_and_an_older_owner_profile_drops_it() -> None:
    assert "lab" in ROLE_CONTEXT_KEYS["trader"]
    for role in ("research", "learning", "optimisation", "leader", "engineer"):
        assert "lab" not in ROLE_CONTEXT_KEYS[role]
    older = {role: sorted(keys - {"lab"}) for role, keys in ROLE_CONTEXT_KEYS.items()}
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="scripted", enabled=True, allowed_context_keys=older))
    assert "lab" not in config.subscription.allowed_context_keys["trader"]  # what step L3 adds to the profile
