"""The actual continuous service owns preparation, confinement and failure recovery."""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from tests.integration.test_protected_department_graph import GRAPH
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.application import deployment_runtime
from trade_graph.application.deployment_runtime import ProtectedDeploymentBinding
from trade_graph.application.paper_service import PaperService
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


def binding_flow(tmp_path, monkeypatch, source=GRAPH, extra_sources=()):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=tuple(hashlib.sha256(item.encode()).hexdigest() for item in (source, *extra_sources)),
        operations=("submit_decision", "invoke_model", "apply_role_result"),
    )
    owner = [manifest, source, b"synthetic-owner-only-key-32-bytes-41"]
    # File/mount trust is exercised by deployment-image tests. These fixtures
    # drive the real service, controller, confined processes and durable effects.
    monkeypatch.setattr(deployment_runtime, "_owner_bundle", lambda _directory: tuple(owner))
    runtime = SimpleNamespace(database=flow.db, clock=flow.clock, execution=flow.office.execution,
        office=flow.office, secretary=flow.secretary, engineer=flow.engineer, artifact_runtime=flow.runtime,
        deployment_id="deployment", model_config=flow.config, model_handlers=None, handlers={})
    binding = ProtectedDeploymentBinding(runtime, tmp_path / "owner", api_keys={})

    def prepare():
        handlers = binding.prepare()
        scripted = runtime.model_handlers.gateway.scripted
        original = scripted.complete

        def complete(request):
            payload = {"summary": "Bounded installed Research evidence.", "outcome": "Insufficient sample.",
                "findings": [], "evidence_refs": request.context["evidence_refs"][:20]}
            return original(request.model_copy(update={"context": {**request.context, "scripted_result": payload}}))

        scripted.complete = complete
        return handlers

    service = PaperService(flow.db, flow.office.execution, clock=flow.clock, portfolio_ids=[flow.pid],
        artifact_runtime=flow.runtime, secretary=flow.secretary, schedule_intervals={},
        prepare_runtime=prepare, runtime_ready=binding.ready, tick_interval_seconds=0.01)
    return flow, binding, service, owner


def test_service_executes_real_research_through_confined_owner_deployment(tmp_path, monkeypatch):
    flow, binding, service, _ = binding_flow(tmp_path, monkeypatch)
    identity = flow.add("research")
    asyncio.run(service.run(max_ticks=1))
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    result = flow.db.execute("SELECT document_json FROM role_results WHERE task_id=?", (identity,)).fetchone()[0]
    assert "Confined completion" in result
    assert binding.runtime.model_handlers.gateway.runtime is binding.protected
    assert flow.db.execute("SELECT count(*) FROM protected_rpc_requests WHERE state='APPLIED'").fetchone()[0] == 2
    assert flow.db.execute("SELECT count(*) FROM usage_receipts WHERE synthetic=1").fetchone()[0] == 1
    assert flow.office.budget.remaining("deployment") == 5
    assert not flow.transport.calls


def test_preparation_runs_after_management_under_exclusive_database_ownership(tmp_path, monkeypatch):
    flow, binding, service, _ = binding_flow(tmp_path, monkeypatch)
    original = service.prepare_runtime
    checked = []

    def prepare():
        assert service._lock_fd is not None
        assert service.management_portfolio_ids == (flow.pid,)
        assert not service._ready
        competing = PaperService(flow.db, flow.office.execution, schedule_intervals={})
        with pytest.raises(StaleState, match="owns this database"):
            asyncio.run(competing.start())
        checked.append(True)
        return original()

    service.prepare_runtime = prepare
    asyncio.run(service.run(max_ticks=1))
    assert checked == [True]
    assert binding.ready()


def test_failed_confined_graph_keeps_next_task_queued_and_restart_management_only(tmp_path, monkeypatch):
    source = GRAPH.replace('assert "guard" not in', 'open("/etc/passwd").read()\n    assert "guard" not in')
    flow, binding, service, _ = binding_flow(tmp_path, monkeypatch, source)
    identities = (flow.add("research"), flow.add("research"))
    asyncio.run(service.run(max_ticks=2))
    assert sorted(flow.row(identity)["status"] for identity in identities) == ["FAILED", "QUEUED"]
    second = next(identity for identity in identities if flow.row(identity)["status"] == "QUEUED")
    assert flow.row(second)["status"] == "QUEUED"
    assert flow.row(second)["attempts_used"] == 0
    assert not binding.ready()
    generation = binding.protected.controller.status()["generation"]
    asyncio.run(service.run(max_ticks=1))
    assert flow.row(second)["status"] == "QUEUED"
    assert flow.row(second)["attempts_used"] == 0
    assert binding.protected.controller.status()["generation"] == generation
    assert flow.db.execute("SELECT count(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.office.budget.remaining("deployment") == 5


def test_owner_bundle_drift_before_start_cannot_admit_or_activate(tmp_path, monkeypatch):
    flow, binding, service, owner = binding_flow(tmp_path, monkeypatch)
    owner[2] = b"synthetic-rotated-owner-key-32-bytes-43"
    with pytest.raises(AuthorityDenied, match="changed during service startup"):
        asyncio.run(service.run(max_ticks=1))
    assert flow.db.execute("SELECT count(*) FROM protected_runtime_instances").fetchone()[0] == 0
    assert flow.db.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0
    assert not binding.ready()


def test_owner_drift_stops_claims_but_keeps_management_active(tmp_path, monkeypatch):
    flow, binding, service, owner = binding_flow(tmp_path, monkeypatch)
    identity = flow.add("research")

    async def scenario():
        await service.start()
        owner[1] += "\n# changed approved bytes"
        tick = await service.tick()
        assert tick.management[flow.pid] == "running"
        assert tick.completed == 0
        await service.stop()

    asyncio.run(scenario())
    assert flow.row(identity)["status"] == "QUEUED"
    assert flow.row(identity)["attempts_used"] == 0
    assert flow.db.execute("SELECT count(*) FROM model_invocations").fetchone()[0] == 0
    assert not binding.ready()


def test_restart_keeps_the_current_approved_release_instead_of_reactivating_configured_source(tmp_path, monkeypatch):
    alternate = GRAPH + "\n# separately owner-approved release"
    flow, binding, service, _ = binding_flow(tmp_path, monkeypatch, extra_sources=(alternate,))
    flow.add("research")
    asyncio.run(service.run(max_ticks=1))
    controller = binding.protected.controller
    controller.admit_release(release_id="alternate-owner-release", source_text=alternate)
    controller.activate_release("alternate-owner-release")
    generation = controller.status()["generation"]
    asyncio.run(service.run(max_ticks=1))
    assert controller.status()["active_release_id"] == "alternate-owner-release"
    assert controller.status()["generation"] == generation
    assert binding.ready()
    assert flow.db.execute("SELECT count(*) FROM usage_receipts").fetchone()[0] == 1
