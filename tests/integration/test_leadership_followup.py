"""Positive action matrix, durable Secretary delivery and scoped API projections."""

import json

import pytest
from pydantic import ValidationError
from tests.integration.test_activation import _ready
from tests.integration.test_leadership_workflow import flow as flow
from tests.leadership_support import reply

from trade_graph.api.app import _changes
from trade_graph.application.authority import paper_mandate
from trade_graph.application.leadership import GatewayRole
from trade_graph.application.secretary import Secretary
from trade_graph.contracts.leadership import DepartmentReply


def test_gateway_mandate_resource_and_schedule_actions(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    before = dict(db.execute("SELECT * FROM deployment_budget").fetchone())
    tighter = paper_mandate(pid, revision=2, gross="0.40", asset="0.20")
    gateway.scripted.outputs["leader"] = reply([ref], [
        {"kind": "mandate", "mandate": tighter.model_dump(mode="json")},
        {"kind": "allocate", "role": "learning", "budget": {"amount": "0.5", "currency": "EUR"}},
        {"kind": "schedule", "role": "leader", "interval_seconds": 120},
    ])
    assert worker.run_available({"leader": handler}) == 1
    assert db.execute("SELECT status FROM tasks WHERE task_id = ?", (tid,)).fetchone()[0] == "SUCCEEDED"
    assert office.execution.authority.active_mandate(pid).revision == 2
    assert dict(db.execute("SELECT * FROM deployment_budget").fetchone()) == before
    assert db.execute("SELECT amount FROM role_allocations WHERE role = 'learning'").fetchone()[0] == "0.5"
    # Re-instantiating the Secretary must respect the persisted 120-second schedule.
    restarted = Secretary(office.execution, office.scheduler)
    assert restarted.scheduled(pid) is None
    clock.advance(120)
    followup = restarted.scheduled(pid)
    assert followup is not None
    gateway.scripted.outputs["leader"] = reply([ref])
    assert worker.run_available({"leader": handler}) == 1
    assert db.execute("SELECT interval_seconds FROM schedules WHERE name = 'leader-review'").fetchone()[0] == 120


@pytest.mark.parametrize("case", ["scope", "envelope", "reserve", "root", "attempts"])
def test_gateway_action_bounds_preserve_policy_budget_and_tasks(flow, case):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    if case in {"scope", "envelope"}:
        mandate = paper_mandate("another" if case == "scope" else pid, revision=2,
                                gross="0.95" if case == "envelope" else "0.4",
                                asset="0.9" if case == "envelope" else "0.2")
        action = {"kind": "mandate", "mandate": mandate.model_dump(mode="json")}
    elif case == "reserve":
        action = {"kind": "allocate", "role": "learning", "budget": {"amount": "2", "currency": "EUR"}}
    else:
        action = {"kind": "assign", "role": "research", "objective": "Review",
                  "budget": {"amount": "2" if case == "root" else "0.2", "currency": "EUR"}}
        if case == "attempts":
            action["max_attempts"] = 4
    before = dict(db.execute("SELECT * FROM deployment_budget").fetchone())
    gateway.scripted.outputs["leader"] = reply([ref], [action])
    assert worker.run_available({"leader": handler}) == 1
    assert db.execute("SELECT status FROM tasks WHERE task_id = ?", (tid,)).fetchone()[0] == "FAILED"
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    assert office.execution.authority.active_mandate(pid).revision == 1
    assert dict(db.execute("SELECT * FROM deployment_budget").fetchone()) == before


def test_digest_backlog_is_pinned_oldest_first_and_not_routed_twice(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply([ref])
    worker.run_available({"leader": handler})
    for index in range(41):
        secretary.report(pid, role="research", kind="finding", summary=f"Evidence {index}",
                         evidence_refs=[f"source-{index}"], source_key=f"batch-{index}")
    batches = []
    for _ in range(3):
        assert secretary.scheduled(pid) is None
        batches.append(secretary.digest(pid)["digest_id"])
    assert len(set(batches)) == 3
    for expected in batches:
        clock.advance(3600)
        followup = secretary.scheduled(pid)
        row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (followup,)).fetchone()
        assert json.loads(row["input_json"])["digest_id"] == expected
        doc = json.loads(db.execute("SELECT document_json FROM secretary_digests WHERE digest_id = ?",
                                    (expected,)).fetchone()[0])
        assert secretary.route(pid, doc) == followup
        gateway.scripted.outputs["leader"] = reply(doc["evidence_refs"])
        assert worker.run_available({"leader": handler}) == 1
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 4


def test_leader_failure_does_not_escalate_into_unbounded_new_roots(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = {"failure": "refusal"}
    worker.run_available({"leader": handler})
    for _ in range(4):
        secretary.process(pid)
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM secretary_reports WHERE kind = 'failed'").fetchone()[0] == 1


def test_largest_department_reply_fits_persisted_report(flow):
    db, clock, pid, office, secretary, gateway, handler, ref, tid, worker = flow
    gateway.scripted.outputs["leader"] = reply([ref], [
        {"kind": "assign", "role": "research", "objective": "Review evidence",
         "budget": {"amount": "1", "currency": "EUR"}},
    ])
    worker.run_available({"leader": handler})
    gateway.scripted.outputs["research"] = {"summary": "a" * 1500, "outcome": "b" * 1400, "evidence_refs": [ref]}
    department = GatewayRole(office, secretary, gateway, deployment_id="deployment", price_card_id="scripted-review")
    assert worker.run_available({"research": department}) == 1
    assert db.execute("SELECT status FROM tasks WHERE role = 'research'").fetchone()[0] == "SUCCEEDED"
    row = db.execute("SELECT document_json FROM secretary_reports WHERE kind = 'outcome'").fetchone()
    document = json.loads(row[0])
    assert len(document["summary"]) <= 3000
    with pytest.raises(ValidationError):
        DepartmentReply(summary="a" * 1501, outcome="b", evidence_refs=[ref])


def test_change_projection_is_scoped_to_runtime_portfolio(tmp_path):
    db, pid, versions, baseline, result = _ready(tmp_path)
    runtime = type("R", (), {"database": db, "portfolio_id": "other"})()
    assert _changes(runtime)["candidates"] == []
    runtime.portfolio_id = pid
    assert [row["candidate_id"] for row in _changes(runtime)["candidates"]] == [result.candidate_id]
