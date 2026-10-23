"""Subscription admission controls the actual service without API fallback."""

import asyncio
import hashlib
from types import SimpleNamespace

from tests.integration.test_protected_department_graph import GRAPH
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.adapters.models.subscription import SubscriptionConfig
from trade_graph.application import deployment_runtime
from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding
from trade_graph.application.paper_service import PaperService
from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.application.subscription_runtime import SubscriptionRuntimeConfig
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


def blocked_flow(tmp_path, monkeypatch):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"),
    )
    monkeypatch.setattr(deployment_runtime, "_owner_bundle",
                        lambda _directory: (manifest, GRAPH, b"synthetic-owner-only-key-32-bytes-41"))
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider="claude_subscription", model="claude-sonnet-4-6", enabled=False))
    admission = SubscriptionAdmission(config, None, {
        "ready": False, "selected_provider": "claude_subscription", "blockers": ["synthetic missing native login"],
        "quota": {}, "cost_status": "unknown", "inference_attempts": 0,
    }, "1" * 64)
    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile",
                        lambda _directory: admission)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda _directory, _digest: True)
    runtime = SimpleNamespace(database=flow.db, clock=flow.clock, execution=flow.office.execution,
        office=flow.office, secretary=flow.secretary, engineer=flow.engineer, artifact_runtime=flow.runtime,
        deployment_id="deployment", model_config=None, model_handlers=None, handlers={})
    binding = ProtectedDeploymentBinding(runtime, tmp_path / "owner", api_keys={})
    return flow, runtime, binding


def test_blocked_subscription_keeps_actual_service_management_and_no_api_fallback(tmp_path, monkeypatch):
    flow, runtime, binding = blocked_flow(tmp_path, monkeypatch)
    identity = flow.add("research")
    service = PaperService(flow.db, runtime.execution, clock=flow.clock, portfolio_ids=[flow.pid],
        artifact_runtime=flow.runtime, secretary=flow.secretary, schedule_intervals={},
        prepare_runtime=binding.prepare, runtime_ready=binding.ready,
        subscription_provider=runtime.subscription_provider)
    outcome = asyncio.run(service.run(max_ticks=1))
    assert not outcome["failures"]
    assert not binding.ready()
    assert runtime.handlers == {}
    assert flow.row(identity)["status"] == "QUEUED"
    assert flow.row(identity)["attempts_used"] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert not flow.transport.calls


def test_changed_subscription_profile_keeps_management_but_blocks_graph_ready(tmp_path, monkeypatch):
    flow, runtime, binding = blocked_flow(tmp_path, monkeypatch)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda _directory, _digest: False)
    assert not binding.ready()


def test_changed_subscription_profile_before_start_does_not_stop_protection(tmp_path, monkeypatch):
    flow, runtime, binding = blocked_flow(tmp_path, monkeypatch)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda _directory, _digest: False)
    service = PaperService(flow.db, runtime.execution, clock=flow.clock, portfolio_ids=[flow.pid],
        artifact_runtime=flow.runtime, secretary=flow.secretary, schedule_intervals={},
        prepare_runtime=binding.prepare, runtime_ready=binding.ready,
        subscription_provider=runtime.subscription_provider)
    outcome = asyncio.run(service.run(max_ticks=1))
    assert not outcome["failures"]
    assert not binding.ready()
    assert runtime.handlers == {}
    assert not flow.transport.calls
