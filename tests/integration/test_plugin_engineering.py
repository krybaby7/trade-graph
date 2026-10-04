"""Actual commissioned generation, confined validation, billing and crash recovery.

Owner grants, provider usage and private receipt keys here are synthetic fixtures.
No deployed capability grant, production activation or economic claim is made.
"""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from tests.integration.test_engineer import _stack, _task
from tests.integration.test_engineering_workflow import Flow, expire_task_lease, finish_bounded
from tests.integration.test_plugin_runtime_shadow import IMPROVED, comparison
from tests.integration.test_plugin_staging import GOOD, KEY, prepared
from tests.leadership_support import commission

from trade_graph.adapters.engineering.artifact_files import content_hash
from trade_graph.adapters.engineering.plugin_artifacts import sha256
from trade_graph.adapters.engineering.plugin_runtime import (
    PluginRuntimeBuilder,
    runtime_builder_sha256,
    runtime_environment_sha256,
)
from trade_graph.application.authority import paper_owner_policy, seed_paper_authority
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.plugin_engineering import (
    CLASS_NAME,
    SOURCE_PATH,
    CommissionedPluginHandler,
    OfflinePluginEngineer,
    PluginCommissionPolicy,
    plugin_controller_sha256,
)
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure
from trade_graph.domain.money import Money


@pytest.fixture(autouse=True)
def restore_synthetic_private_store_modes(tmp_path):
    yield
    for directory, _, _ in os.walk(tmp_path, followlinks=False):
        Path(directory).chmod(0o700)


def patch(source=IMPROVED, path=SOURCE_PATH):
    return {"files": [{"path": path, "content": source}], "summary": "Implement bounded synthetic numeric features."}


def flow(tmp_path, *, owner_grant=True, max_steps=3, max_spend="1", source=IMPROVED, maximum_recoveries=3):
    clock, db, pid, original_source, original_engineer, versions = _stack(tmp_path)
    store, replay, protected, corpus = prepared(tmp_path)
    builder = PluginRuntimeBuilder(store, replay_validator=replay, expected_builder_sha256=runtime_builder_sha256(),
                                   expected_environment_sha256=runtime_environment_sha256(),
                                   expected_protected_manifest_sha256=protected, receipt_key=KEY)
    staged = store.stage(GOOD, baseline_release_id="initial-seed", baseline_source_sha256="0" * 64,
                         protected_manifest_sha256=protected, validation_corpus_sha256=corpus)
    receipt = replay.validate(staged.manifest.build_digest)
    baseline_build = builder.build(staged.manifest.build_digest, replay_receipt_sha256=receipt)
    shadow, _, _, shadow_policy = comparison(store, builder, protected, baseline_build)
    baseline_hash = content_hash({SOURCE_PATH: GOOD})
    policy = PluginCommissionPolicy(
        baseline_release_id="baseline-1", baseline_artifact_sha256=baseline_hash,
        baseline_runtime_build_sha256=baseline_build["runtime_build_sha256"],
        baseline_source_sha256=sha256(GOOD.encode()),
        protected_manifest_sha256=protected, shadow_policy_sha256=shadow_policy.sha256,
        maximum_local_recoveries=maximum_recoveries,
    )
    engineer = OfflinePluginEngineer(db, clock, original_engineer.ledger, shadow, policy=policy,
                                     expected_policy_sha256=policy.sha256,
                                     expected_controller_sha256=plugin_controller_sha256(), receipt_key=KEY)
    versions.ensure(pid, "baseline-1", baseline_hash)
    # This explicit synthetic policy is never installed by runtime assembly.
    authority = seed_paper_authority(db, clock, pid)
    if owner_grant:
        authority.install_policy(paper_owner_policy(revision_id="synthetic-plugin-grant").model_copy(
            update={"allowed_change_classes": [CLASS_NAME]}), role="owner")
    proposal = _task(clock, pid, baseline_hash, baseline_version="baseline-1", allowed_classes=[CLASS_NAME],
                     allowed_paths=[SOURCE_PATH], max_steps=max_steps,
                     max_spend=Money(amount=max_spend, currency="EUR"))
    if not owner_grant:
        return engineer, proposal, authority
    office, secretary, gateway, _, root = commission(engineer, pid, proposal)
    task_id = db.execute("SELECT worker_task_id FROM engineering_commissions").fetchone()[0]
    workspace = tmp_path / "unused-model-workspace"
    handler = CommissionedPluginHandler(engineer, office.scheduler, gateway, deployment_id="deployment",
                                        price_card_id="scripted-review", workspace_root=workspace)
    worker = RoleWorker(office.scheduler, owner="fixture-worker", system_version_id=baseline_hash,
                        reconcile=lambda: None)
    gateway.attempts.clear()
    gateway.scripted.outputs["engineer"] = patch(source)
    return Flow(clock, db, pid, original_source, engineer, versions, baseline_hash, office, secretary, gateway,
                proposal.record_id, task_id, root, workspace, handler, worker)


def restart(current, monkeypatch):
    gateway = ModelGateway(current.gateway.budget, paid_calls_enabled=False)
    monkeypatch.setattr(gateway.scripted, "complete", lambda _: pytest.fail("repeated gateway dispatch after crash"))
    current.gateway = gateway
    current.handler = CommissionedPluginHandler(current.engineer, current.office.scheduler, gateway,
                                                deployment_id="deployment", price_card_id="scripted-review",
                                                workspace_root=current.workspace)


def proof(current, candidate_id):
    row = current.db.execute("SELECT report_json FROM candidate_attestations WHERE candidate_id = ?",
                             (candidate_id,)).fetchone()
    return json.loads(row[0])


def test_commissioned_generated_source_is_independently_built_shadowed_and_billed(tmp_path):
    current = flow(tmp_path)
    current.engineer.ledger.deposit(current.pid, "USD", Decimal("10000"), "retained-capital")
    financial = [dict(row) for row in current.db.execute("SELECT * FROM ledger_events").fetchall()]
    assert current.run() == 1
    output = current.output()
    assert output["_status"] == "SUCCEEDED", output
    report = current.engineer.verify_candidate(output["candidate_id"])
    assert report["status"] == "READY"
    assert report["class_name"] == CLASS_NAME
    assert report["production_authorization"] is report["live_authorization"] is False
    assert report["economic_evidence"] == "not_evaluated"
    assert set(report["evidence"]) == {
        "stage_build_digest", "replay_receipt_sha256", "build_receipt_sha256",
        "runtime_build_sha256", "shadow_receipt_sha256",
    }
    runtime = report["evidence"]["runtime_build_sha256"]
    manifest, source = current.engineer.builder.load(runtime)
    assert source == IMPROVED
    assert manifest.baseline_source_sha256 == sha256(GOOD.encode())
    assert current.engineer.builder.evaluate(runtime, {"price_eur": "200", "spread_bps": "3",
                                                      "position_quantity": "0"})["features"] == {"signal": "0.5"}
    assert len(current.receipts()) == len(current.gateway.attempts) == 1
    assert report["cost"]["reservation_id"] == current.receipts()[0]["reservation_id"]
    assert report["cost"]["usage_receipts"][0]["native_cost"] == current.receipts()[0]["native_cost"]
    assert Decimal(current.receipts()[0]["native_cost"]) > 0
    assert current.versions.current_hash(current.pid) == current.baseline
    assert [dict(row) for row in current.db.execute("SELECT * FROM ledger_events").fetchall()] == financial
    assert not current.workspace.exists()
    assert current.run() == 0
    # Separate controller identity and executable class cannot masquerade as R1.
    with pytest.raises(ValidationFailure, match="trusted attestation"):
        current.versions.activate(current.pid, {"candidate_id": output["candidate_id"]})
    assert current.versions.current_hash(current.pid) == current.baseline


def test_model_context_excludes_independent_cases_expectations_receipt_keys_and_host_paths(tmp_path, monkeypatch):
    current = flow(tmp_path)
    complete = current.gateway.scripted.complete

    def inspect(request):
        job = current.db.execute("SELECT document_json FROM engineering_jobs WHERE task_id = ?",
                                 (current.tid,)).fetchone()
        persisted = json.loads(job[0])
        assert persisted["phase"] == "REQUESTING"
        invocation = current.db.execute("SELECT state FROM model_invocations WHERE task_id = ?",
                                         (current.tid,)).fetchone()
        assert invocation[0] == "DISPATCHED"
        encoded = json.dumps(request.context)
        assert "held-out-1" not in encoded and "expected_features" not in encoded
        assert str(current.engineer.store.root) not in encoded and KEY.decode() not in encoded
        assert request.context["source_files"] == {SOURCE_PATH: GOOD}
        assert request.max_tool_calls == 0
        assert "numeric" in request.instructions or "Decimal" in request.instructions
        return complete(request)

    monkeypatch.setattr(current.gateway.scripted, "complete", inspect)
    assert current.run() == 1
    assert current.output()["_status"] == "SUCCEEDED"


@pytest.mark.parametrize("source,failure", [
    ("def propose(snapshot):\n    return {'signal':'1'}\n", "independent_replay_rejected"),
    (GOOD, "independent_shadow_rejected"),
    ("def propose(snapshot):\n    open('/etc/passwd').read()\n    return {'signal':'0'}\n",
     "independent_replay_rejected"),
])
def test_negative_attempt_retains_independent_evidence_and_repair_has_separate_cost(tmp_path, source, failure):
    current = flow(tmp_path, source=source)
    assert current.run() == 1
    rejected = current.output()["candidate_id"]
    first = current.engineer.verify_candidate(rejected)
    assert first["status"] == "FAILED" and failure in first["failures"]
    assert len(current.receipts()) == 1
    assert current.row()["status"] == "QUEUED"
    current.gateway.scripted.outputs["engineer"] = patch()
    current.clock.advance(31)
    assert current.run() == 1
    assert current.output()["_status"] == "SUCCEEDED"
    accepted = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert accepted["status"] == "READY"
    assert accepted["cost"]["reservation_id"] != first["cost"]["reservation_id"]
    assert {row["attempt_kind"] for row in current.receipts()} == {"primary", "patch_repair"}
    assert current.engineer.verify_candidate(rejected)["status"] == "FAILED"
    assert current.db.execute("SELECT count(*) FROM engineering_attempts").fetchone()[0] == 2


@pytest.mark.parametrize("path", ["src/trade_graph/kernel/authority.py", "../plugins/feature.py",
                                 "artifacts/context_policy.json", "plugins/other.py"])
def test_wrong_paths_are_billed_and_rejected_before_executable_source_is_staged(tmp_path, path):
    current = flow(tmp_path, max_steps=1)
    current.gateway.scripted.outputs["engineer"] = patch(path=path)
    assert current.run() == 1
    assert current.output()["_status"] == "FAILED"
    report = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert report["evidence"] == {}
    assert report["failures"] == ["protected_validation_AuthorityDenied"]
    assert len(current.receipts()) == 1
    assert not current.workspace.exists()


def test_gateway_schema_repairs_and_attempt_envelope_are_shared_with_plugin_generation(tmp_path):
    current = flow(tmp_path, max_steps=2)
    current.gateway.scripted.outputs["engineer"] = {"files": [], "summary": "invalid"}
    result = finish_bounded(current)
    assert result["_status"] == "FAILED"
    assert current.db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
    assert len(current.receipts()) == 2
    assert {row["attempt_kind"] for row in current.receipts()} == {"primary", "schema_repair"}
    assert current.row()["attempts_used"] == 2


def test_zero_commissioned_budget_does_not_dispatch_or_stage_candidate(tmp_path):
    current = flow(tmp_path, max_spend="0")
    assert current.run() == 1
    assert current.output()["_status"] == "BLOCKED_BUDGET"
    assert current.gateway.attempts == [] and current.receipts() == []
    assert current.db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


def test_default_policy_and_provider_paths_keep_broader_engineer_authority_closed(tmp_path):
    engineer, proposal, authority = flow(tmp_path, owner_grant=False)
    assert CLASS_NAME not in authority.active_policy().allowed_change_classes
    with pytest.raises(AssertionError, match="FAILED"):
        commission(engineer, proposal.portfolio_id, proposal)
    assert engineer.database.execute("SELECT count(*) FROM engineering_commissions").fetchone()[0] == 0
    with pytest.raises(AuthorityDenied, match="T18/T21/owner"):
        CommissionedPluginHandler(None, None, None, provider="openai")


def test_crash_after_retained_replay_resumes_locally_without_repeat_model_or_replay(tmp_path, monkeypatch):
    current = flow(tmp_path)
    save = current.engineer._save_attempt

    class Crash(BaseException):
        pass

    def interrupt_after_replay(attempt_id, details, **kwargs):
        save(attempt_id, details, **kwargs)
        raise Crash

    monkeypatch.setattr(current.engineer, "_save_attempt", interrupt_after_replay)
    with pytest.raises(Crash):
        current.run()
    attempt = current.db.execute("SELECT * FROM engineering_attempts").fetchone()
    assert attempt["state"] == "RUNNING"
    retained_replay = json.loads(attempt["details_json"])["evidence"]["replay_receipt_sha256"]
    assert len(current.receipts()) == 1
    expire_task_lease(current)
    monkeypatch.setattr(current.engineer, "_save_attempt", save)
    monkeypatch.setattr(current.engineer.builder.replay_validator, "validate",
                        lambda _: pytest.fail("repeated retained replay"))
    restart(current, monkeypatch)
    assert current.run() == 1
    assert current.output()["_status"] == "SUCCEEDED"
    report = current.engineer.verify_candidate(current.output()["candidate_id"])
    assert report["evidence"]["replay_receipt_sha256"] == retained_replay
    assert report["local_recoveries"] == 1
    assert current.db.execute("SELECT count(*) FROM engineering_attempts").fetchone()[0] == 1
    assert len(current.receipts()) == 1


def test_revocation_after_gateway_bill_keeps_cost_and_blocks_candidate_publication(tmp_path, monkeypatch):
    current = flow(tmp_path)
    complete = current.gateway.scripted.complete

    def revoke(request):
        result = complete(request)
        authority = seed_paper_authority(current.db, current.clock, current.pid)
        authority.install_policy(paper_owner_policy(revision_id="revoked-synthetic-grant"), role="owner")
        return result

    monkeypatch.setattr(current.gateway.scripted, "complete", revoke)
    assert current.run() == 1
    assert current.output()["_status"] == "FAILED"
    assert len(current.receipts()) == 1
    assert current.db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
    assert current.versions.current_hash(current.pid) == current.baseline


def test_candidate_receipt_key_proof_substitution_and_billing_changes_fail_restart_verification(tmp_path):
    current = flow(tmp_path)
    assert current.run() == 1
    candidate = current.output()["candidate_id"]
    report = current.engineer.verify_candidate(candidate)
    original_key = current.engineer._key
    current.engineer._key = b"different-synthetic-receipt-key-32bytes"
    with pytest.raises(PermissionError, match="authentication"):
        current.engineer.verify_candidate(candidate)
    current.engineer._key = original_key
    attestation = proof(current, candidate)
    attestation["evidence"]["shadow_receipt_sha256"] = "f" * 64
    current.db.execute("UPDATE candidate_attestations SET report_json = ? WHERE candidate_id = ?",
                       (json.dumps(attestation), candidate))
    with pytest.raises(AuthorityDenied, match="substitution"):
        current.engineer.verify_candidate(candidate)
    attestation["evidence"]["shadow_receipt_sha256"] = report["evidence"]["shadow_receipt_sha256"]
    current.db.execute("UPDATE candidate_attestations SET report_json = ? WHERE candidate_id = ?",
                       (json.dumps(attestation), candidate))
    current.db.execute("UPDATE usage_receipts SET native_cost = '0' WHERE receipt_id = ?",
                       (report["cost"]["usage_receipts"][0]["receipt_id"],))
    with pytest.raises(AuthorityDenied, match="billing evidence changed"):
        current.engineer.verify_candidate(candidate)


def test_missing_malformed_or_rebound_records_have_typed_verification_refusals(tmp_path):
    current = flow(tmp_path)
    assert current.run() == 1
    candidate = current.output()["candidate_id"]
    original = current.db.execute("SELECT document_json FROM candidates WHERE candidate_id = ?",
                                  (candidate,)).fetchone()[0]
    changed = json.loads(original)
    changed["change_id"] = "missing-change"
    current.db.execute("UPDATE candidates SET document_json = ? WHERE candidate_id = ?",
                       (json.dumps(changed), candidate))
    with pytest.raises(AuthorityDenied, match="missing.*change task"):
        current.engineer.verify_candidate(candidate)
    current.db.execute("UPDATE candidates SET document_json = ? WHERE candidate_id = ?", (original, candidate))
    attestation = proof(current, candidate)
    changed_proof = {key: value for key, value in attestation.items() if key != "commission_receipt_sha256"}
    current.db.execute("UPDATE candidate_attestations SET report_json = ? WHERE candidate_id = ?",
                       (json.dumps(changed_proof), candidate))
    with pytest.raises(AuthorityDenied, match="malformed"):
        current.engineer.verify_candidate(candidate)
    current.db.execute("UPDATE candidate_attestations SET report_json = ? WHERE candidate_id = ?",
                       (json.dumps(attestation), candidate))
    current.db.execute("UPDATE candidate_attestations SET content_hash = ? WHERE candidate_id = ?",
                       ("0" * 64, candidate))
    with pytest.raises(AuthorityDenied, match="attestation column identity"):
        current.engineer.verify_candidate(candidate)
    current.db.execute("UPDATE candidate_attestations SET content_hash = ? WHERE candidate_id = ?",
                       (attestation["content_hash"], candidate))
    current.db.execute("DELETE FROM engineering_commissions WHERE change_id = ?", (current.change_id,))
    with pytest.raises(AuthorityDenied, match="Leader commission"):
        current.engineer.verify_candidate(candidate)


def test_lost_gateway_dispatch_retains_unknown_usage_without_generation_or_reissue(tmp_path, monkeypatch):
    current = flow(tmp_path)

    class LostResponse(BaseException):
        pass

    def lose_response(request):
        raise LostResponse

    monkeypatch.setattr(current.gateway.scripted, "complete", lose_response)
    with pytest.raises(LostResponse):
        current.run()
    invocation = current.db.execute("SELECT * FROM model_invocations WHERE task_id = ?", (current.tid,)).fetchone()
    assert invocation["state"] == "DISPATCHED" and invocation["result_json"] is None
    expire_task_lease(current)
    restart(current, monkeypatch)
    assert current.run() == 1
    assert current.output()["_status"] == "WAITING_EXTERNAL"
    reservation = current.db.execute("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                                     (invocation["reservation_id"],)).fetchone()
    assert reservation[0] == "UNCERTAIN"
    assert current.receipts() == []
    assert current.db.execute("SELECT count(*) FROM engineering_attempts").fetchone()[0] == 0
    assert current.db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


def test_commission_cannot_replace_class_path_or_independent_baseline(tmp_path):
    engineer, proposal, _ = flow(tmp_path, owner_grant=False)
    for changes in ({"allowed_classes": ["artifact_config"]}, {"allowed_paths": ["plugins/"]},
                    {"baseline_version": "caller-baseline"}, {"baseline_hash": "0" * 64}):
        with pytest.raises(AuthorityDenied, match="exact plugin class/path"):
            engineer.propose(proposal.portfolio_id, proposal.model_copy(update=changes))
    assert engineer.database.execute("SELECT count(*) FROM change_tasks").fetchone()[0] == 0


def test_exhausted_local_recovery_preserves_bill_without_unbounded_validation(tmp_path, monkeypatch):
    current = flow(tmp_path, maximum_recoveries=0)
    save = current.engineer._save_attempt

    class Crash(BaseException):
        pass

    def crash(attempt_id, details, **kwargs):
        save(attempt_id, details, **kwargs)
        raise Crash

    monkeypatch.setattr(current.engineer, "_save_attempt", crash)
    with pytest.raises(Crash):
        current.run()
    retained = json.loads(current.db.execute("SELECT details_json FROM engineering_attempts").fetchone()[0])
    assert "replay_receipt_sha256" in retained["evidence"]
    expire_task_lease(current)
    monkeypatch.setattr(current.engineer, "_save_attempt", save)
    monkeypatch.setattr(current.engineer.builder, "build", lambda *a, **k: pytest.fail("exhausted recovery ran build"))
    restart(current, monkeypatch)
    assert current.run() == 1
    assert current.output()["_status"] == "FAILED"
    assert "recovery envelope exhausted" in current.output()["reason"]
    assert len(current.receipts()) == 1
    assert current.db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
