"""Actual Secretary → scheduler → worker → gateway → durable effects and consultations."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.leadership_support import reply, stack

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.authority import paper_owner_policy
from trade_graph.application.leadership import GatewayRole, LeaderHandler
from trade_graph.application.ledger import Ledger
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import ModelResult
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied


@pytest.fixture
def flow(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    db = Database(tmp_path / "flow.sqlite")
    ledger = Ledger(db, clock)
    pid = ledger.create_portfolio(reporting_currency="EUR")
    VersionController(db, clock).ensure(pid, "v1", "baseline")
    office, secretary, gateway, handler = stack(db, clock, ledger, pid)
    ref = secretary.report(
        pid,
        role="research",
        kind="finding",
        summary="Observe costs, not just returns.",
        evidence_refs=["fixture-source"],
        source_key="source-1",
    )
    tid = secretary.scheduled(pid)
    worker = RoleWorker(office.scheduler, owner="worker", system_version_id="baseline", reconcile=lambda: None)
    return db, clock, pid, office, secretary, gateway, handler, ref, tid, worker


def test_gateway_decision_assignment_and_evidence_persist(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref],
        [
            {
                "kind": "assign",
                "role": "research",
                "objective": "Review fee assumptions",
                "budget": {"amount": "0.5", "currency": "EUR"},
            }
        ],
    )
    assert worker.run_available({"leader": handler}) == 1
    record = db.execute("SELECT * FROM leader_decisions").fetchone()
    assert record["state"] == "APPLIED"
    decision = json.loads(record["document_json"])
    assert decision["evidence_refs"] == [ref]
    assert decision["resources_committed"] and decision["review_criteria"] and decision["snapshot_id"]
    child = db.execute("SELECT * FROM tasks WHERE role = 'research'").fetchone()
    assert child["root_task_id"] == child["parent_id"] == tid
    assert child["allocated_spend"] == "0.5"
    receipt = db.execute(
        """SELECT b.*, r.synthetic AS receipt_synthetic FROM budget_reservations b
        JOIN usage_receipts r USING (reservation_id)"""
    ).fetchone()
    assert receipt["task_id"] == receipt["root_task_id"] == tid
    assert receipt["receipt_synthetic"] == 1
    assert office.budget.remaining("deployment") == Decimal("5")
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_consultation_dispatch_completes_and_returns_under_same_root(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref],
        [
            {
                "kind": "consult",
                "role": "research",
                "objective": "Explain cost sensitivity",
                "budget": {"amount": "1", "currency": "EUR"},
                "followup_budget": {"amount": "0.4", "currency": "EUR"},
                "max_attempts": 1,
            }
        ],
    )
    assert worker.run_available({"leader": handler}) == 1
    gateway.scripted.outputs["research"] = {
        "evidence_refs": [ref],
        "summary": "Fees dominate tiny positions.",
        "outcome": "Evaluate fewer higher-quality opportunities.",
    }
    department = GatewayRole(office, secretary, gateway, deployment_id="deployment", price_card_id="scripted-review")
    assert worker.run_available({"research": department}) == 1
    result = db.execute("SELECT * FROM role_results WHERE role = 'research'").fetchone()
    result_doc = json.loads(result["document_json"])
    followup = db.execute("SELECT * FROM tasks WHERE task_id = ?", (result_doc["followup_task_id"],)).fetchone()
    assert followup["root_task_id"] == tid and followup["parent_id"] == result["task_id"]
    assert followup["allocated_spend"] == "0.4"
    report = result_doc["report_id"]
    gateway.scripted.outputs["leader"] = reply([report])
    assert worker.run_available({"leader": handler}) == 1
    decision = db.execute(
        "SELECT document_json FROM leader_decisions WHERE task_id = ?", (followup["task_id"],)
    ).fetchone()
    assert report in json.loads(decision[0])["evidence_refs"]
    assert {r[0] for r in db.execute("SELECT root_task_id FROM budget_reservations")} == {tid}
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 3
    assert worker.run_available({"leader": handler, "research": department}) == 0
    assert len(gateway.attempts) == 3


def test_applied_decision_recovers_before_another_model_attempt(flow, monkeypatch):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref],
        [{"kind": "assign", "role": "research", "objective": "Review", "budget": {"amount": "0.2", "currency": "EUR"}}],
    )
    finish = office.scheduler.finish

    def crash(*args, **kwargs):
        raise RuntimeError("crash after effects committed")

    monkeypatch.setattr(office.scheduler, "finish", crash)
    with pytest.raises(RuntimeError):
        worker.run_available({"leader": handler})
    db.execute("UPDATE tasks SET attempts_used = max_attempts WHERE task_id = ?", (tid,))
    clock.advance(31)
    monkeypatch.setattr(office.scheduler, "finish", finish)
    assert (
        worker.run_available(
            {
                "leader": LeaderHandler(
                    office, secretary, gateway, deployment_id="deployment", price_card_id="scripted-review"
                )
            }
        )
        == 1
    )
    assert len(gateway.attempts) == 1
    assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE role = 'research'").fetchone()[0] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "owner_halt",
        "stale_policy",
        "stale_mandate",
        "stale_budget",
        "stale_pause",
        "stale_version",
        "failure",
        "exhaustion",
        "invalid_evidence",
    ],
)
def test_rejected_decision_has_no_actions_or_false_hold(flow, monkeypatch, fault):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref],
        [{"kind": "assign", "role": "research", "objective": "Review", "budget": {"amount": "0.2", "currency": "EUR"}}],
    )
    complete = gateway.scripted.complete

    def change(request):
        result = complete(request)
        if fault == "stale_policy":
            office.execution.authority.install_policy(paper_owner_policy(revision_id="2"), role="owner")
        elif fault == "stale_mandate":
            mandate = office.execution.authority.active_mandate(pid)
            office.execution.authority.install_mandate(
                mandate.model_copy(update={"revision": 2, "mandate_id": "new"}), role="owner"
            )
        elif fault == "stale_budget":
            db.execute("UPDATE deployment_budget SET root_limit = '0.1'")
        elif fault == "stale_pause":
            office.execution.set_pause(pid, "MANAGE_ONLY", "owner", "halt during inference")
        elif fault == "stale_version":
            db.execute("UPDATE active_versions SET artifact_hash = 'new'")
        elif fault == "failure":
            return ModelResult(ok=False, failure="refusal", message="scripted refusal", usage=result.usage)
        return result

    monkeypatch.setattr(gateway.scripted, "complete", change)
    if fault == "owner_halt":
        office.execution.set_pause(pid, "MANAGE_ONLY", "owner", "halt before inference")
    elif fault == "exhaustion":
        db.execute("UPDATE tasks SET allocated_spend = '0' WHERE task_id = ?", (tid,))
    elif fault == "invalid_evidence":
        gateway.scripted.outputs["leader"]["evidence_refs"] = ["invented"]
    assert worker.run_available({"leader": handler}) == 1
    result = json.loads(db.execute("SELECT document_json FROM role_results").fetchone()[0])
    assert result["_status"] == ("BLOCKED_BUDGET" if fault == "exhaustion" else "FAILED")
    assert "hold" not in result
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    if fault in {"owner_halt", "exhaustion"}:
        assert len(gateway.attempts) == 0
    else:
        assert len(gateway.attempts) == 1
        assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1


def test_invalid_later_action_rolls_back_earlier_assignment(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref],
        [
            {
                "kind": "assign",
                "role": "research",
                "objective": "Valid first",
                "budget": {"amount": "0.2", "currency": "EUR"},
            },
            {"kind": "allocate", "role": "leader", "budget": {"amount": "999", "currency": "EUR"}},
        ],
    )
    assert worker.run_available({"leader": handler}) == 1
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    assert office.budget.allowance("deployment") == Decimal("5")


def test_secretary_coalesces_and_restarts_without_duplicate_routing(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    for index in range(3):
        secretary.report(
            pid,
            role="learning",
            kind="incident",
            summary="Review correlated outcomes.",
            evidence_refs=[ref],
            source_key=f"incident-{index}",
            material=True,
        )
    first = secretary.process(pid)
    assert len(first["groups"]["learning:incident"]) == 3
    before = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    restarted = type(secretary)(office.execution, office.scheduler)
    second = restarted.process(pid)
    assert first["digest_id"] == second["digest_id"]
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before


def test_leader_may_change_and_resume_only_its_own_pause(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply(
        [ref], [{"kind": "pause", "profile": "PAUSE_DECISIONS", "reason": "review"}]
    )
    worker.run_available({"leader": handler})
    assert office.execution.pause(pid)["originator"] == "leader"
    clock.advance(3601)
    secretary.scheduled(pid)
    gateway.scripted.outputs["leader"] = reply(
        [ref], [{"kind": "pause", "profile": "RUNNING", "reason": "review complete"}]
    )
    assert worker.run_available({"leader": handler}) >= 1
    assert office.execution.profile(pid) == "RUNNING"
    office.execution.set_pause(pid, "MANAGE_ONLY", "system", "safety")
    with pytest.raises(AuthorityDenied):
        office.set_pause(pid, "RUNNING", "override")
