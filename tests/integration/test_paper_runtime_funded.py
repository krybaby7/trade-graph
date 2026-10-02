"""Installed paper assembly consumes explicit owner funding and protected model setup.

Provider calls use an injected recording transport with fabricated test usage;
these tests do not execute a credentialed provider or public/exchange request.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_artifact_consumers import _choice
from tests.integration.test_engineer import POLICY
from tests.integration.test_execution import _quote, _rules
from tests.integration.test_runtime_models import RecordingTransport, _card
from tests.leadership_support import reply

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.runtime_models import MODEL_ROLES
from trade_graph.application.worker import RoleWorker
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock
from trade_graph.paper_runtime import assemble_paper_runtime, load_runtime_config

REGISTERED_CLASSES = ["artifact_config", "context_policy", "prompt", "schedule", "report_template",
                      "approved_model_routing"]


def _owner_client(runtime):
    token, _csrf = issue_session(runtime.database, runtime.clock, "owner")
    return TestClient(create_app(runtime)), {"Authorization": f"Bearer {token}"}


def _funded_setup(tmp_path, monkeypatch, capsys, *, owner_paid=True, runtime_paid=True, keys=None):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    initial = assemble_paper_runtime(path, clock=clock)
    client, headers = _owner_client(initial)
    response = client.post("/api/v1/owner/budgets", headers=headers, json={
        "request_id": "explicit-owner-funding", "expected_revision": 0,
        "total": "5", "period": "5", "priority_reserve": "1", "daily": "1.50", "root": "1.50",
        "roles": {role: "1" for role in MODEL_ROLES},
    })
    assert response.status_code == 200, response.json()
    response = client.post("/api/v1/owner/config", headers=headers, json={
        "request_id": "explicit-owner-permission", "expected_revision": 1,
        "paid_calls_enabled": owner_paid, "allowed_change_classes": REGISTERED_CLASSES,
    })
    assert response.status_code == 200, response.json()
    initial.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.90"), source="synthetic-reference",
                              kind="reference", stale=False)
    initial.database.close()
    config_path = tmp_path / "private-model-config.json"
    config_path.write_text(json.dumps({
        "models": {"paid_calls_enabled": runtime_paid, "approved_price_card_ids": ["runtime-card"],
                   "role_routes": {role: "runtime-card" for role in MODEL_ROLES},
                   "fx_rate": "0.90", "fx_buffer": "1.02"},
        "price_cards": [_card("runtime-card").model_dump(mode="json")],
        "public_data_enabled": False,
    }))
    config_path.chmod(0o600)
    transport = RecordingTransport()
    runtime = assemble_paper_runtime(path, config=load_runtime_config(config_path), clock=clock,
        api_keys={"openai": "synthetic-private-credential"} if keys is None else keys, transport=transport)
    assert not transport.calls
    assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    return runtime, transport


def _worker(runtime):
    return RoleWorker(runtime.scheduler, owner="assembled-paper-worker",
        system_version_id=runtime.versions.current_hash(runtime.portfolio_id),
        reconcile=lambda: asyncio.run(runtime.execution.reconcile()), artifact_runtime=runtime.artifact_runtime)


def _turn(runtime, worker, role):
    runtime.secretary.process(runtime.portfolio_id, route=False)
    task_id = runtime.scheduler.add_task(role=role, objective="Review the bounded installed-paper evidence.",
        portfolio_id=runtime.portfolio_id, max_attempts=1, allocated_spend=Decimal("1"))
    assert worker.run_available({role: runtime.handlers[role]}) == 1
    row = runtime.database.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row


def test_owner_api_grants_all_six_registered_artifact_classes(tmp_path, monkeypatch, capsys):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    path = tmp_path / "paper.sqlite"
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    runtime = assemble_paper_runtime(path, clock=clock)
    try:
        client, headers = _owner_client(runtime)
        response = client.post("/api/v1/owner/config", headers=headers, json={
            "request_id": "registered-routing-grant", "expected_revision": 0,
            "allowed_change_classes": REGISTERED_CLASSES,
        })
        assert response.status_code == 200, response.json()
        assert set(response.json()["policy"]["allowed_change_classes"]) == set(REGISTERED_CLASSES)
        assert runtime.execution.authority.active_policy().paid_calls_enabled is False
        response = client.post("/api/v1/owner/config", headers=headers, json={
            "request_id": "forbidden-code-grant", "expected_revision": 1,
            "allowed_change_classes": ["unrestricted_code"],
        })
        assert response.status_code == 403
        assert set(runtime.execution.authority.active_policy().allowed_change_classes) == set(REGISTERED_CLASSES)
    finally:
        runtime.database.close()


def test_installed_funded_runtime_runs_all_six_actual_roles_without_checkout(tmp_path, monkeypatch, capsys):
    runtime, transport = _funded_setup(tmp_path, monkeypatch, capsys)
    monkeypatch.chdir(tmp_path)
    try:
        assert set(runtime.handlers) == set(MODEL_ROLES)
        assert runtime.paid_calls_enabled is True and runtime.live_enabled is False
        runtime.execution.register_instrument(_rules())
        runtime.execution.save_observation(_quote(runtime.clock, "99", "100", observation_id="runtime-source"))
        transport.outputs["ResearchReply"] = lambda context: {
            "evidence_refs": context["evidence_refs"][:20], "summary": "Inspect source liquidity.",
            "outcome": "One source is insufficient evidence.", "findings": [{"source_ref": "runtime-source",
                "question": "Visible spread?", "claim": "One-unit spread at the snapshot.",
                "counterevidence": "One observation cannot demonstrate an edge.",
                "invalidation": "New observations supersede this snapshot.", "expires_after_seconds": 3600}],
        }
        worker = _worker(runtime)
        research = _turn(runtime, worker, "research")
        assert research["status"] == "SUCCEEDED", research["output_json"]
        transport.outputs["TraderReply"] = _choice()
        trader = _turn(runtime, worker, "trader")
        assert trader["status"] == "SUCCEEDED", trader["output_json"]
        decision_id = json.loads(trader["output_json"])["decision_id"]
        transport.outputs["LearningReply"] = lambda context: {
            "evidence_refs": context["evidence_refs"][:20], "summary": "Retain process separately from outcome.",
            "outcome": "One observation remains tentative.", "lessons": [{"lesson_id": None,
                "observation": "Held from the available snapshot.", "supporting_cases": [decision_id],
                "counterexamples": ["Future prices can rise after this hold."],
                "explanation": "A bounded hold is a valid discretionary choice.",
                "proposed_improvement": "Keep relevant source context bounded.", "validation_method": "Forward sample.",
                "scope": "BTC/USD paper", "sample_note": "One decision.", "linked_decisions": [decision_id],
                "confidence_category": "tentative", "status": "tentative", "process_assessment": "valid_thesis",
                "outcome_sign": "unknown"}],
        }
        learning = _turn(runtime, worker, "learning")
        assert learning["status"] == "SUCCEEDED", learning["output_json"]
        transport.outputs["OptimisationReply"] = lambda context: {
            "evidence_refs": context["evidence_refs"][:20], "summary": "Evaluate bounded context.",
            "outcome": "A measurable proposal needs independent checks.", "proposals": [{
                "issue": "Unbounded irrelevant lessons.", "resources": "Existing owner-funded envelope.",
                "interval": "Next review.", "modification": "Reduce general context cap.",
                "expected_benefit": "Bounded input.", "quality_risk": "Rare evidence may be omitted.",
                "validation_metrics": ["required obligations retained"]}],
            "changes": [{"objective": "Reduce the general lesson cap within fixed obligations.",
                "allowed_classes": ["context_policy"], "allowed_paths": ["artifacts/context_policy.json"],
                "invariants": ["mandate_obligations", "active_safety"], "max_spend_eur": "0.5", "max_steps": 1,
                "test_plan": "Independent confined artifact grammar check.",
                "success_criteria": "Required obligations retained.", "rollback_criteria": "Prior pointer only.",
                "expires_after_seconds": 3600}],
        }
        optimisation = _turn(runtime, worker, "optimisation")
        assert optimisation["status"] == "SUCCEEDED", optimisation["output_json"]
        change_id = json.loads(optimisation["output_json"])["change_ids"][0]
        transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
            {"kind": "commission", "change_id": change_id}])
        leader = _turn(runtime, worker, "leader")
        assert leader["status"] == "SUCCEEDED", leader["output_json"]
        transport.outputs["EngineerPatch"] = {"summary": "Keep a bounded general lesson cap.", "files": [
            {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}
        assert worker.run_available({"engineer": runtime.handlers["engineer"]}) == 1
        engineer = runtime.database.execute("SELECT * FROM tasks WHERE role = 'engineer'").fetchone()
        assert engineer["status"] == "SUCCEEDED", engineer["output_json"]
        assert runtime.database.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert runtime.database.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1
        assert runtime.database.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
        assert runtime.database.execute("SELECT state FROM candidates").fetchone()[0] == "READY"
        assert runtime.database.execute(
            "SELECT COUNT(*) FROM candidate_attestations WHERE exit_code = 0").fetchone()[0] == 1
        receipts = runtime.database.execute("SELECT r.*,b.role FROM usage_receipts r "
                                            "JOIN budget_reservations b USING(reservation_id)").fetchall()
        assert len(transport.calls) == len(receipts) == 6
        assert {row["role"] for row in receipts} == set(MODEL_ROLES)
        assert all(row["synthetic"] == 0 and row["provider"] == "openai" for row in receipts)
        assert runtime.budget.remaining("deployment") < Decimal("5")
        for call in transport.calls:
            assert "synthetic-private-credential" not in json.dumps(call["body"])
    finally:
        runtime.database.close()


@pytest.mark.parametrize("keys", [{}, {"anthropic": "synthetic-unrelated-credential"}])
def test_funded_installed_runtime_missing_selected_provider_key_refuses_without_cost(keys, tmp_path,
                                                                                    monkeypatch, capsys):
    runtime, transport = _funded_setup(tmp_path, monkeypatch, capsys, keys=keys)
    try:
        assert set(runtime.handlers) == set(MODEL_ROLES)
        row = _turn(runtime, _worker(runtime), "trader")
        assert row["status"] == "FAILED"
        assert "private provider credential" in row["output_json"]
        assert not transport.calls
        assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
        assert runtime.budget.remaining("deployment") == Decimal("5")
    finally:
        runtime.database.close()


@pytest.mark.parametrize("options", [{"owner_paid": False}, {"runtime_paid": False}])
def test_installed_runtime_requires_both_owner_and_private_paid_gates(options, tmp_path, monkeypatch, capsys):
    runtime, transport = _funded_setup(tmp_path, monkeypatch, capsys, **options)
    try:
        assert runtime.paid_calls_enabled is False
        assert runtime.handlers == {} and runtime.model_handlers is None
        assert not transport.calls
        assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    finally:
        runtime.database.close()


def test_owner_api_revokes_installed_paid_handlers_without_restart(tmp_path, monkeypatch, capsys):
    runtime, transport = _funded_setup(tmp_path, monkeypatch, capsys)
    try:
        transport.outputs["TraderReply"] = _choice()
        worker = _worker(runtime)
        allowed = _turn(runtime, worker, "trader")
        assert allowed["status"] == "SUCCEEDED", allowed["output_json"]
        client, headers = _owner_client(runtime)
        revision = client.get("/api/v1/owner/config", headers=headers).json()["revision"]
        response = client.post("/api/v1/owner/config", headers=headers, json={
            "request_id": "revoke-real-api-permission", "expected_revision": revision,
            "paid_calls_enabled": False,
        })
        assert response.status_code == 200, response.json()
        denied = _turn(runtime, worker, "trader")
        assert denied["status"] == "FAILED"
        assert "owner has not enabled paid calls" in denied["output_json"]
        assert len(transport.calls) == 1
        assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
    finally:
        runtime.database.close()
