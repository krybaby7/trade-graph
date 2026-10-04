"""Actual protected Leader/Gateway/Engineer commissions for confined presentation."""

import json
import os
from pathlib import Path

import pytest
from tests.integration.test_application_projection import KEY, V1, V2, baseline, setup
from tests.integration.test_engineer import _task
from tests.integration.test_engineering_workflow import Flow, expire_task_lease, finish_bounded
from tests.leadership_support import commission, reply
from tests.leadership_support import stack as leadership_stack

from trade_graph.adapters.engineering.artifact_files import content_hash
from trade_graph.adapters.engineering.plugin_runtime import runtime_environment_sha256
from trade_graph.application.activation import VersionController
from trade_graph.application.application_engineering import (
    CLASS_NAME,
    SOURCE_PATH,
    ApplicationCommissionPolicy,
    CommissionedApplicationHandler,
    OfflineApplicationEngineer,
    application_engineering_controller_sha256,
)
from trade_graph.application.authority import paper_owner_policy, seed_paper_authority
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure
from trade_graph.domain.money import Money


@pytest.fixture(scope="module")
def environment_pin():
    return runtime_environment_sha256()


@pytest.fixture(autouse=True)
def restore_private_modes(tmp_path):
    yield
    for directory, _, _ in os.walk(tmp_path, followlinks=False):
        Path(directory).chmod(0o700)


def patch(source=V2, path=SOURCE_PATH):
    return {"files": [{"path": path, "content": source}], "summary": "Group synthetic reports with incidents visible."}


def flow(tmp_path, environment_pin, *, source=V2, max_steps=3, max_spend="1", owner_grant=True,
         maximum_recoveries=3):
    projection, store, arguments, snapshot, secretary, financial = setup(tmp_path, environment_pin)
    db, clock, ledger, _, _, pid, _ = financial
    build, _, _ = baseline(projection, snapshot)
    artifact = content_hash({SOURCE_PATH: V1})
    policy = ApplicationCommissionPolicy(baseline_release_id="projection-baseline-1",
                                        baseline_artifact_sha256=artifact, baseline_build_sha256=build,
                                        baseline_source_sha256=projection.policy.baseline_source_sha256,
                                        projection_policy_sha256=projection.policy_pin,
                                        maximum_local_recoveries=maximum_recoveries)
    engineer = OfflineApplicationEngineer(db, clock, ledger, projection, policy=policy,
                                         expected_policy_sha256=policy.sha256,
                                         expected_controller_sha256=application_engineering_controller_sha256(),
                                         receipt_key=KEY)
    versions = VersionController(db, clock)
    versions.ensure(pid, policy.baseline_release_id, artifact)
    authority = seed_paper_authority(db, clock, pid)
    if owner_grant:
        authority.install_policy(paper_owner_policy(revision_id="synthetic-projection-grant").model_copy(
            update={"allowed_change_classes": [CLASS_NAME]}), role="owner")
    # Acknowledge the older projection-input digest through the real protected
    # Leader path, so the next bounded digest contains the new proposal.
    if owner_grant:
        first_office, first_secretary, first_gateway, first_handler = leadership_stack(db, clock, ledger, pid)
        first_gateway.scripted.outputs["leader"] = reply(snapshot["report_ids"] if "report_ids" in snapshot
                                                        else [r["report_id"] for r in snapshot["reports"]])
        first_secretary.scheduled(pid)
        first_worker = RoleWorker(first_office.scheduler, owner="fixture-worker",
                                  system_version_id=artifact, reconcile=lambda: None)
        assert first_worker.run_available({"leader": first_handler}) == 1
        clock.advance(3601)
    proposal = _task(clock, pid, artifact, baseline_version=policy.baseline_release_id,
                     allowed_classes=[CLASS_NAME], allowed_paths=[SOURCE_PATH], max_steps=max_steps,
                     max_spend=Money(amount=max_spend, currency="EUR"))
    if not owner_grant:
        return engineer, proposal, authority
    office, _, gateway, _, root = commission(engineer, pid, proposal)
    tid = db.execute("SELECT worker_task_id FROM engineering_commissions").fetchone()[0]
    workspace = tmp_path / "candidate-has-no-workspace"
    handler = CommissionedApplicationHandler(engineer, office.scheduler, gateway, deployment_id="deployment",
                                            price_card_id="scripted-review", workspace_root=workspace)
    worker = RoleWorker(office.scheduler, owner="fixture-worker", system_version_id=artifact, reconcile=lambda: None)
    gateway.attempts.clear()
    gateway.scripted.outputs["engineer"] = patch(source)
    current = Flow(clock, db, pid, None, engineer, versions, artifact, office, secretary, gateway,
                   proposal.record_id, tid, root, workspace, handler, worker)
    current.projection, current.projection_arguments, current.snapshot = projection, arguments, snapshot
    current.execution, current.runtime = financial[3], financial[6]
    return current


def restart(current, monkeypatch):
    gateway = ModelGateway(current.gateway.budget, paid_calls_enabled=False)
    monkeypatch.setattr(gateway.scripted, "complete", lambda _: pytest.fail("reissued durable model generation"))
    current.handler = CommissionedApplicationHandler(current.engineer, current.office.scheduler, gateway,
                                                    deployment_id="deployment", price_card_id="scripted-review",
                                                    workspace_root=current.workspace)
    current.gateway = gateway


def test_selected_application_generated_source_is_confined_authenticated_and_billed(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin)
    financial = [dict(row) for row in current.db.execute("SELECT * FROM ledger_events")]
    assert current.run() == 1
    assert current.output()["_status"] == "SUCCEEDED"
    report = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert report["kind"] == "commissioned_application" and report["status"] == "READY"
    assert report["contract"] == "secretary_projection/v2"
    assert report["production_authorization"] is report["paid_authorization"] is report["live_authorization"] is False
    build = report["evidence"]["build_sha256"]
    assert current.projection.load(build)[1] == V2
    validation = current.projection.verify_validation(report["evidence"]["validation_receipt_sha256"], build=build)
    assert all(attempt["exit_code"] == 0 and attempt["matched"] for attempt in validation["attempts"])
    assert len(current.gateway.attempts) == len(current.receipts()) == 1
    assert report["cost"]["usage_receipts"][0]["status"] == "committed"
    assert report["cost"]["cost_allocations"][0]["portfolio_id"] == current.pid
    assert current.versions.current_hash(current.pid) == current.baseline
    assert current.projection.status()["phase"] == "LEGACY"
    assert [dict(row) for row in current.db.execute("SELECT * FROM ledger_events")] == financial
    assert not current.workspace.exists()
    with pytest.raises(ValidationFailure, match="trusted attestation"):
        current.versions.activate(current.pid, {"candidate_id": report["candidate_id"]})


def test_parent_expected_cases_keys_paths_and_receipts_absent_from_model_context(
    tmp_path, environment_pin, monkeypatch
):
    current = flow(tmp_path, environment_pin)
    complete = current.gateway.scripted.complete

    def inspect(request):
        encoded = json.dumps(request.context)
        assert request.context["source_files"] == {SOURCE_PATH: V1}
        assert "expected_v2" not in encoded and "Grouped reports" not in encoded
        assert str(current.engineer.store.root) not in encoded and KEY.decode() not in encoded
        assert request.max_tool_calls == 0
        state = current.db.execute("SELECT state FROM model_invocations WHERE task_id=?", (current.tid,)).fetchone()[0]
        assert state == "DISPATCHED"
        return complete(request)

    monkeypatch.setattr(current.gateway.scripted, "complete", inspect)
    assert current.run() == 1 and current.output()["_status"] == "SUCCEEDED"


@pytest.mark.parametrize("source", [
    V2.replace('group["report_ids"].append(report["report_id"])', 'group["report_ids"].append("missing-report")'),
    V2.replace('labels = ', 'open("/etc/passwd").read()\n    labels = '),
    V2.replace('labels = ', 'import socket\n    socket.socket()\n    labels = '),
])
def test_failed_confined_source_retains_cost_and_separately_billed_repair(tmp_path, environment_pin, source):
    current = flow(tmp_path, environment_pin, source=source)
    assert current.run() == 1
    first = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert first["status"] == "FAILED"
    assert first["failures"] == ["independent_application_projection_rejected"]
    assert len(current.receipts()) == 1
    current.gateway.scripted.outputs["engineer"] = patch()
    current.clock.advance(31)
    assert current.run() == 1 and current.output()["_status"] == "SUCCEEDED"
    assert len(current.receipts()) == 2
    assert {row["attempt_kind"] for row in current.receipts()} == {"primary", "patch_repair"}
    assert current.projection.status()["phase"] == "LEGACY"


@pytest.mark.parametrize("path", ["src/trade_graph/kernel/authority.py", "../applications/secretary_projection.py",
                                  "applications/migration.py"])
def test_wrong_path_has_receipt_but_no_executable_stage(tmp_path, environment_pin, path):
    current = flow(tmp_path, environment_pin, max_steps=1)
    current.gateway.scripted.outputs["engineer"] = patch(path=path)
    assert current.run() == 1 and current.output()["_status"] == "FAILED"
    report = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert report["evidence"] == {} and len(current.receipts()) == 1


def test_zero_budget_and_bounded_schema_repairs(tmp_path, environment_pin):
    current = flow(tmp_path / "zero", environment_pin, max_spend="0")
    assert current.run() == 1 and current.output()["_status"] == "BLOCKED_BUDGET"
    assert len(current.receipts()) == len(current.gateway.attempts) == 0
    other = flow(tmp_path / "schema", environment_pin, max_steps=2)
    other.gateway.scripted.outputs["engineer"] = {"files": [], "summary": "bad"}
    assert finish_bounded(other)["_status"] == "FAILED"
    assert len(other.receipts()) == 2
    assert {row["attempt_kind"] for row in other.receipts()} == {"primary", "schema_repair"}


def test_no_default_class_admission_or_funded_provider_route(tmp_path, environment_pin):
    engineer, task, authority = flow(tmp_path / "default", environment_pin, owner_grant=False)
    assert CLASS_NAME not in authority.active_policy().allowed_change_classes
    for changed in ({"allowed_paths": ["applications/"]}, {"allowed_classes": ["artifact_config"]},
                    {"baseline_hash": "0" * 64}, {"baseline_version": "caller-baseline"}):
        with pytest.raises(AuthorityDenied, match="exact application"):
            engineer.propose(task.portfolio_id, task.model_copy(update=changed))
    current = flow(tmp_path / "provider", environment_pin)
    with pytest.raises(AuthorityDenied, match="funded application"):
        CommissionedApplicationHandler(current.engineer, current.office.scheduler, current.gateway,
                                       provider="openai", deployment_id="deployment", price_card_id="scripted-review",
                                       workspace_root=current.workspace)
    current.gateway.paid_calls_enabled = True
    with pytest.raises(AuthorityDenied, match="credential-free"):
        CommissionedApplicationHandler(current.engineer, current.office.scheduler, current.gateway,
                                       deployment_id="deployment", price_card_id="scripted-review",
                                       workspace_root=current.workspace)


def test_crash_after_validation_recovers_without_model_or_child_reexecution(tmp_path, environment_pin, monkeypatch):
    current = flow(tmp_path, environment_pin)
    save = current.engineer._save_attempt

    class Crash(BaseException):
        pass

    def crash(attempt_id, details, **kwargs):
        save(attempt_id, details, **kwargs)
        raise Crash

    monkeypatch.setattr(current.engineer, "_save_attempt", crash)
    with pytest.raises(Crash):
        current.run()
    evidence = json.loads(current.db.execute("SELECT details_json FROM engineering_attempts").fetchone()[0])["evidence"]
    assert evidence["validation_receipt_sha256"] and len(current.receipts()) == 1
    expire_task_lease(current)
    monkeypatch.setattr(current.engineer, "_save_attempt", save)
    monkeypatch.setattr(current.projection, "validate", lambda _: pytest.fail("repeated retained confined validation"))
    restart(current, monkeypatch)
    assert current.run() == 1 and current.output()["_status"] == "SUCCEEDED"
    report = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert report["evidence"] == evidence and report["local_recoveries"] == 1
    assert len(current.receipts()) == 1


def test_lost_gateway_dispatch_is_unknown_and_not_reissued(tmp_path, environment_pin, monkeypatch):
    current = flow(tmp_path, environment_pin)

    class Lost(BaseException):
        pass

    monkeypatch.setattr(current.gateway.scripted, "complete", lambda _: (_ for _ in ()).throw(Lost()))
    with pytest.raises(Lost):
        current.run()
    invocation = current.db.execute("SELECT * FROM model_invocations WHERE task_id=?", (current.tid,)).fetchone()
    assert invocation["state"] == "DISPATCHED"
    expire_task_lease(current)
    restart(current, monkeypatch)
    assert current.run() == 1 and current.output()["_status"] == "WAITING_EXTERNAL"
    assert current.db.execute("SELECT state FROM budget_reservations WHERE reservation_id=?",
                              (invocation["reservation_id"],)).fetchone()[0] == "UNCERTAIN"
    assert len(current.receipts()) == 0
    assert current.db.execute("SELECT count(*) FROM engineering_attempts").fetchone()[0] == 0


def test_full_usage_and_generated_source_cost_binding_cannot_be_rebound(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin)
    assert current.run() == 1
    candidate = current.output()["candidate_id"]
    report = current.engineer.verify_candidate(candidate)
    receipt = report["cost"]["usage_receipts"][0]
    current.db.execute("UPDATE usage_receipts SET usage_json='{}' WHERE receipt_id=?", (receipt["receipt_id"],))
    with pytest.raises(AuthorityDenied, match="billing evidence changed"):
        current.engineer.verify_candidate(candidate)
    current.db.execute("UPDATE usage_receipts SET usage_json=? WHERE receipt_id=?",
                       (receipt["usage_json"], receipt["receipt_id"]))
    current.engineer._key = b"different-private-synthetic-receipt-key-42"
    with pytest.raises(PermissionError, match="authentication"):
        current.engineer.verify_candidate(candidate)
