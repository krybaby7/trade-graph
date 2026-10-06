"""Real protected startup consumes synthetic subscription outcomes without credentialed calls."""

import asyncio
import hashlib
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
