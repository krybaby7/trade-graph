"""Real protected startup consumes synthetic subscription outcomes without credentialed calls."""

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.integration.test_protected_department_graph import GRAPH
from tests.integration.test_runtime_models import RuntimeFlow
from tests.integration.test_subscription_departments import SyntheticCli

from trade_graph.adapters.models.subscription import (
    CliOutcome,
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionReadiness,
)
from trade_graph.application import deployment_runtime
from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding
from trade_graph.application.paper_service import PaperService
from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.application.subscription_runtime import SubscriptionRuntimeConfig
from trade_graph.contracts.models import ModelResult
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


def subscription_service(tmp_path, monkeypatch, *, quota_exhausted=False):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"),
    )
    monkeypatch.setattr(deployment_runtime, "_owner_bundle",
                        lambda _directory: (manifest, GRAPH, b"synthetic-owner-only-key-32-bytes-41"))
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="claude-sonnet-5-5", enabled=True))
    readiness = SubscriptionReadiness("claude_subscription", "2.1.285", True, (), {},
                                      "subscription", "linux-bubblewrap")
    cli = SyntheticCli()
    cli.outputs["research"] = {"summary": "Synthetic bounded subscription evidence.",
        "outcome": "Insufficient sample.", "findings": [], "evidence_refs": []}
    if quota_exhausted:
        def exhausted(request, **kwargs):
            cli.requests.append(request)
            return CliOutcome('{"type":"result","is_error":true,"error":"rate_limit"}', 1)
        cli.execute = exhausted
    adapter = SubscriptionAdapter(config.subscription, readiness, cli)
    admission = SubscriptionAdmission(config, adapter, {
        **readiness.public_status(), "selected_provider": "claude_subscription", "inference_attempts": 0,
    }, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda _directory: admission)
    current = [True]
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda _directory, _digest: current[0])
    runtime = SimpleNamespace(database=flow.db, clock=flow.clock, portfolio_id=flow.pid,
        execution=flow.office.execution, office=flow.office, secretary=flow.secretary, engineer=flow.engineer,
        artifact_runtime=flow.runtime, deployment_id="deployment", model_config=None, model_handlers=None,
        handlers={}, config=None)
    binding = ProtectedDeploymentBinding(runtime, tmp_path / "owner", api_keys={})
    service = PaperService(flow.db, runtime.execution, clock=flow.clock, portfolio_ids=[flow.pid],
        artifact_runtime=flow.runtime, secretary=flow.secretary, schedule_intervals={},
        prepare_runtime=binding.prepare, runtime_ready=binding.ready,
        subscription_provider=runtime.subscription_provider, tick_interval_seconds=0.01)
    return flow, runtime, binding, service, cli, current


def test_actual_service_consumes_protected_subscription_handlers_without_api_billing(tmp_path, monkeypatch):
    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    identity = flow.add("research")
    outcome = asyncio.run(service.run(max_ticks=1))
    assert not outcome["failures"] and outcome["completed"] == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    assert runtime.subscription_provider == "claude_subscription" and binding.ready()
    assert set(runtime.handlers) == {"research", "trader", "learning", "leader", "optimisation", "engineer"}
    assert runtime.model_handlers.gateway.runtime is binding.protected
    assert len(cli.requests) == 1
    assert "Confined completion" in flow.row(identity)["output_json"]
    receipt = flow.db.execute("SELECT * FROM subscription_invocations").fetchone()
    assert receipt["actual_model"] == "claude-sonnet-5-5" and receipt["state"] == "COMPLETED"
    assert receipt["cost_status"] == "unknown" and receipt["actual_cost_native"] is None
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert flow.office.budget.remaining("deployment") == 5
    assert not flow.transport.calls


def test_subscription_prepare_runs_only_after_service_owns_management_database(tmp_path, monkeypatch):
    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    prepare = service.prepare_runtime
    observations = []

    def protected_prepare():
        assert service._lock_fd is not None and not service._ready
        assert service.management_portfolio_ids == (flow.pid,)
        assert runtime.model_handlers is None
        handlers = prepare()
        observations.append(set(handlers))
        return handlers

    service.prepare_runtime = protected_prepare
    flow.add("research")
    asyncio.run(service.run(max_ticks=1))
    assert observations == [set(runtime.handlers)] and len(cli.requests) == 1
    assert binding.ready()


def test_subscription_quota_stops_new_graph_work_and_keeps_service_management(tmp_path, monkeypatch):
    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch, quota_exhausted=True)
    first, second = flow.add("research"), flow.add("research")
    outcome = asyncio.run(service.run(max_ticks=2))
    assert not outcome["failures"]
    assert outcome["management"][flow.pid] == "running"
    statuses = {identity: flow.row(identity)["status"] for identity in (first, second)}
    assert sorted(statuses.values()) == ["FAILED", "QUEUED"]
    queued = next(identity for identity, state in statuses.items() if state == "QUEUED")
    assert flow.row(queued)["attempts_used"] == 0 and len(cli.requests) == 1
    assert not binding.ready() and not runtime.model_handlers.router.readiness(flow.pid)["ready"]
    assert flow.db.execute("SELECT ai_paused FROM subscription_provider_state WHERE "
                           "provider='claude_subscription'").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert not flow.transport.calls
    asyncio.run(service.run(max_ticks=1))
    assert flow.row(queued)["status"] == "QUEUED" and len(cli.requests) == 1


def test_subscription_profile_drift_blocks_roles_while_service_protects_portfolio(tmp_path, monkeypatch):
    flow, _runtime, binding, service, cli, current = subscription_service(tmp_path, monkeypatch)
    identity = flow.add("research")

    async def scenario():
        await service.start()
        current[0] = False
        tick = await service.tick()
        assert tick.management[flow.pid] == "running" and tick.completed == 0
        await service.stop()

    asyncio.run(scenario())
    assert flow.row(identity)["status"] == "QUEUED" and flow.row(identity)["attempts_used"] == 0
    assert not binding.ready() and not cli.requests and not flow.transport.calls


def test_declared_subscription_startup_refuses_api_credentials_and_no_model_fallback(tmp_path, monkeypatch):
    _flow, runtime, _binding, _service, cli, _ = subscription_service(tmp_path, monkeypatch)
    with pytest.raises(AuthorityDenied, match="refuses API"):
        ProtectedDeploymentBinding(runtime, tmp_path / "owner", api_keys={"openai": "synthetic-fixture-only"})
    assert not cli.requests


def test_verified_temporary_subscription_block_can_recover_without_restarting_service(tmp_path, monkeypatch):
    from dataclasses import replace

    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    admission = binding.subscription_admission
    ready = admission.adapter.readiness
    admission.adapter.readiness = replace(ready, ready=False, blockers=("temporary native login unavailable",))
    admission.adapter.readiness_probe = lambda: ready
    binding.subscription_admission = replace(admission, status={**admission.status, "ready": False})
    task = flow.add("research")
    result = asyncio.run(service.run(max_ticks=1))
    assert not result["failures"] and result["completed"] == 1
    assert flow.row(task)["status"] == "SUCCEEDED"
    assert binding.ready() and len(cli.requests) == 1


def test_predispatch_reserve_pause_defers_trader_and_later_uses_fresh_plan(tmp_path, monkeypatch):
    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    cli.outputs["trader"] = {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
        "invalidation": "New evidence.", "experiment": False, "evidence_ids": [], "no_action_reason": "No signal."}
    identity = flow.add("trader")
    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        configured = adapter.config.model_copy(update={"quota_policy_enabled": True})
        adapter.config = runtime.model_handlers.router.config.subscription = configured
        remaining, blocked = [75], [True]
        def readiness():
            return replace(adapter.readiness, quota={"source": "claude-cli",
                "observed_at": flow.clock.now().isoformat(), "windows": {
                    "primary": {"window_duration_mins": 10080, "remaining_percent": remaining[0]}}})
        adapter.readiness_probe = readiness
        adapter.refresh_readiness(force=True)
        original = adapter.invoke
        def invoke(request, **kwargs):
            remaining[0] = 40 if blocked[0] else 75
            return original(request, **kwargs)
        monkeypatch.setattr(adapter, "invoke", invoke)
        first = await service.tick(wait_roles=True)
        assert first.management[flow.pid] == "running" and first.completed == 0
        assert flow.row(identity)["status"] == "QUEUED", flow.row(identity)["output_json"]
        assert flow.row(identity)["attempts_used"] == 0
        assert runtime.execution.profile(flow.pid) == "RUNNING"
        assert not cli.requests
        assert flow.db.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
        plan = flow.db.execute("SELECT state,response_json FROM protected_rpc_requests "
            "WHERE json_extract(scope_json,'$.operation')='invoke_model'").fetchone()
        assert plan["state"] == "REVOKED" and "quota_deferred" in plan["response_json"]
        blocked[0] = False
        flow.clock.advance(31)
        remaining[0] = 75
        adapter.refresh_readiness(force=True)
        second = await service.tick(wait_roles=True)
        assert second.management[flow.pid] == "running"
        assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
        assert flow.row(identity)["attempts_used"] == 1 and len(cli.requests) == 1
        assert flow.db.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] == 1
        assert runtime.execution.profile(flow.pid) == "RUNNING"
        await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,reason", [
    ("rate_limit", "subscription quota exhausted"),
    ("unsupported", "subscription model unavailable"),
])
def test_known_subscription_availability_stop_keeps_management_feed_and_heartbeat(
    tmp_path, monkeypatch, failure, reason,
):
    from trade_graph.contracts.models import Observation

    flow, runtime, binding, service, cli, _ = subscription_service(tmp_path, monkeypatch, quota_exhausted=True)
    first, second = flow.add("trader"), flow.add("research")
    flow.db.execute("UPDATE tasks SET priority=100 WHERE task_id=?", (first,))

    class Feed:
        polls = 0
        def poll(self):
            self.polls += 1
            return [Observation(observation_id=f"pause-feed-{self.polls}", venue="paper", symbol="BTC/USD",
                event_time_utc=flow.clock.now(), available_at_utc=flow.clock.now(),
                bid="99", ask="100", volume="1", kind="quote", source="synthetic-fixture")]
        def close(self):
            pass

    feed = Feed()
    service.public_feed = feed

    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        invoke = adapter.invoke
        def unavailable(request, **kwargs):
            result = invoke(request, **kwargs)
            if failure == "unsupported":
                # Adapter-owned known unsupported-model classification and pause;
                # no model payload or task output supplies this control signal.
                result = result.model_copy(update={"failure": failure, "message": reason})
                journal, invocation_id = kwargs["journal"], kwargs["invocation_id"]
                journal.save_attempt(invocation_id + ":attempt:1", result, "FAILED")
                journal.save(invocation_id, result, "FAILED")
                flow.db.execute("UPDATE subscription_provider_state SET reason=? WHERE provider=?",
                                (reason, adapter.config.provider))
            return result
        monkeypatch.setattr(adapter, "invoke", unavailable)
        first_tick = await service.tick(wait_roles=True, wait_feed=True)
        assert first_tick.management[flow.pid] == "running" and first_tick.observations == 1
        assert flow.row(first)["status"] == "FAILED"
        assert runtime.execution.profile(flow.pid) == "RUNNING"
        receipt = flow.db.execute("SELECT * FROM subscription_invocations WHERE task_id=?", (first,)).fetchone()
        assert receipt["state"] == "FAILED" and receipt["cost_status"] == "unknown"
        assert json.loads(receipt["result_json"])["failure"] == failure
        prior_heartbeat = flow.db.execute("SELECT heartbeat_at FROM graph_service_runs").fetchone()[0]
        flow.clock.advance(1)
        next_tick = await service.tick(wait_roles=True, wait_feed=True)
        assert next_tick.completed == 0 and next_tick.management[flow.pid] == "running"
        assert next_tick.observations == 1 and feed.polls == 2
        assert flow.row(second)["status"] == "QUEUED" and flow.row(second)["attempts_used"] == 0
        status = flow.db.execute("SELECT status,heartbeat_at FROM graph_service_runs").fetchone()
        assert status["status"] == "RUNNING" and status["heartbeat_at"] > prior_heartbeat
        assert not binding.ready() and len(cli.requests) == 1
        assert service._started and not service._stop_requested.is_set()
        assert not flow.transport.calls
        await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["DISPATCHED", "COMPLETED", "FAILED", "UNCERTAIN"])
def test_reserve_marker_cannot_revoke_or_replay_a_started_subscription_invocation(tmp_path, monkeypatch, outcome):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    identity = flow.add("trader")
    cli.outputs["trader"] = {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
        "invalidation": "New evidence.", "experiment": False, "evidence_ids": [], "no_action_reason": "No signal."}
    if outcome == "FAILED":
        cli.outputs["trader"] = {"wrong": "schema"}
    if outcome == "UNCERTAIN":
        cli.interrupt = True

    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        invoke = adapter.invoke
        def invalid_reserve(request, **kwargs):
            if outcome == "DISPATCHED":
                kwargs["journal"].begin(kwargs["invocation_id"], request, adapter.config.provider, {})
                kwargs["journal"].begin_attempt(kwargs["invocation_id"], 1, request, adapter.config.provider)
            else:
                invoke(request, **kwargs)
            return ModelResult(ok=False, failure="quota_reserve", message="incorrect post-dispatch reserve")
        monkeypatch.setattr(adapter, "invoke", invalid_reserve)
        tick = await service.tick(wait_roles=True)
        assert tick.management[flow.pid] == "running"
        assert flow.row(identity)["status"] == ("WAITING_EXTERNAL" if outcome == "UNCERTAIN" else "FAILED")
        assert runtime.execution.profile(flow.pid) == "MANAGE_ONLY"
        row = flow.db.execute("SELECT state FROM subscription_invocations WHERE task_id=?", (identity,)).fetchone()
        assert row["state"] == outcome
        assert flow.db.execute("SELECT state FROM protected_rpc_requests WHERE "
            "json_extract(scope_json,'$.operation')='invoke_model'").fetchone()[0] == "APPLIED"
        flow.clock.advance(31)
        await service.tick(wait_roles=True)
        assert len(cli.requests) == (0 if outcome == "DISPATCHED" else 1)
        assert flow.db.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] == 1
        await service.stop()
    asyncio.run(scenario())


def test_current_reserve_blocks_before_claim_without_stopping_normal_service(tmp_path, monkeypatch):
    flow, runtime, _binding, service, cli, _ = subscription_service(tmp_path, monkeypatch)
    identity = flow.add("trader")
    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        adapter.config = runtime.model_handlers.router.config.subscription = adapter.config.model_copy(
            update={"quota_policy_enabled": True})
        adapter.readiness_probe = lambda: replace(adapter.readiness, quota={"source": "claude-cli",
            "observed_at": flow.clock.now().isoformat(), "windows": {
                "primary": {"window_duration_mins": 10080, "remaining_percent": 40}}})
        adapter.refresh_readiness(force=True)
        for _ in range(2):
            tick = await service.tick(wait_roles=True)
            assert tick.management[flow.pid] == "running" and tick.completed == 0
            assert flow.row(identity)["status"] == "QUEUED" and flow.row(identity)["attempts_used"] == 0
        assert runtime.execution.profile(flow.pid) == "RUNNING"
        assert not cli.requests and not flow.transport.calls
        assert flow.db.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
        assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests").fetchone()[0] == 0
        assert service._started and not service._stop_requested.is_set()
        await service.stop()
    asyncio.run(scenario())


def test_known_provider_stop_preserves_a_concurrent_owner_hold(tmp_path, monkeypatch):
    flow, runtime, _binding, service, _cli, _ = subscription_service(tmp_path, monkeypatch, quota_exhausted=True)
    identity = flow.add("trader")
    async def scenario():
        await service.start()
        adapter = runtime.model_handlers.router.adapter
        invoke = adapter.invoke
        def owner_hold(request, **kwargs):
            result = invoke(request, **kwargs)
            runtime.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "Retain this unrelated owner hold.")
            return result
        monkeypatch.setattr(adapter, "invoke", owner_hold)
        await service.tick(wait_roles=True)
        assert flow.row(identity)["status"] == "FAILED"
        pause = runtime.execution.pause(flow.pid)
        assert pause["originator"] == "owner" and pause["reason"] == "Retain this unrelated owner hold."
        await service.stop()
    asyncio.run(scenario())
