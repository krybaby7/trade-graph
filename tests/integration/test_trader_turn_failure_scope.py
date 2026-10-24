"""An unusable Trader answer fails only that turn; genuine AI unavailability still pauses (owner, 2026-10-13)."""

import asyncio
import json

import pytest
from tests.integration.test_order_refusal import HOLD, _fund
from tests.integration.test_owned_subscription_service import subscription_service
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.contracts.models import ModelResult


def _run_two_turns(service, flow, cli, first_reply):
    """First turn gets ``first_reply``; a valid hold follows in the same event loop."""
    cli.outputs["trader"] = first_reply
    first = flow.add("trader")
    second = []

    async def scenario():
        await service.tick(wait_roles=True)
        cli.outputs["trader"] = HOLD
        second.append(flow.add("trader"))
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    return first, second[0]


@pytest.mark.parametrize("reply", [
    {**HOLD, "evidence_ids": ["invented-research"]},
    {**HOLD, "strategy_id": "invented-strategy"},
], ids=["cites-research-it-was-not-given", "unknown-strategy"])
def test_unusable_trader_answer_fails_that_turn_without_pausing(tmp_path, monkeypatch, reply):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    _fund(flow)
    first, second = _run_two_turns(service, flow, cli, reply)
    assert flow.row(first)["status"] == "FAILED"
    assert json.loads(flow.row(first)["output_json"])["failure_scope"] == "turn"
    assert runtime.execution.pause(flow.pid) is None or runtime.execution.profile(flow.pid) == "RUNNING"
    assert flow.row(second)["status"] == "SUCCEEDED"
    assert len(cli.requests) == 2
    events = flow.db.execute("SELECT payload_json FROM activity_events WHERE kind = 'trader_turn_set_aside'").fetchall()
    assert [json.loads(event[0]) for event in events] == [{"task_id": first, "system_pause": False}]


@pytest.mark.parametrize("failure,pauses", [
    ("validation", False), ("truncation", False), ("refusal", False), ("temporary", False),
    ("credentials", True),
])
def test_model_failure_kind_decides_between_a_failed_turn_and_a_pause(tmp_path, monkeypatch, failure, pauses):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    _fund(flow)
    cli.outputs["trader"] = HOLD
    task_id = flow.add("trader")

    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        invoke = adapter.invoke

        def failing(request, **kwargs):
            invoke(request, **kwargs)
            return ModelResult(ok=False, failure=failure, message="synthetic model failure")

        monkeypatch.setattr(adapter, "invoke", failing)
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    output = json.loads(flow.row(task_id)["output_json"])
    assert flow.row(task_id)["status"] == "FAILED"
    if pauses:
        # A lost AI sign-in is genuine unavailability: positions are managed, no new decisions.
        assert "failure_scope" not in output
        pause = runtime.execution.pause(flow.pid)
        assert pause["profile"] == "MANAGE_ONLY" and pause["reason"] == "role unavailable; manage positions"
    else:
        assert output["failure_scope"] == "turn"
        assert runtime.execution.profile(flow.pid) == "RUNNING"


def test_api_path_marks_a_malformed_trader_reply_as_a_turn_failure(tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.transport.outputs["TraderReply"] = {"action": "hold"}
    task_id = flow.add("trader")
    flow.run("trader")
    assert flow.row(task_id)["status"] == "FAILED"
    assert json.loads(flow.row(task_id)["output_json"])["failure_scope"] == "turn"
