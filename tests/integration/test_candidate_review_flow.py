"""Engineer results must reach a real, evidence-bound Leader decision without manual IDs."""

import json

import pytest
from tests.integration.test_engineer import POLICY, _stack, _task
from tests.leadership_support import commission, reply

from trade_graph.application.worker import RoleWorker


@pytest.fixture
def candidate_flow(tmp_path):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(pid, "v1", baseline)
    proposal = _task(clock, pid, baseline)
    office, secretary, gateway, handler, _ = commission(engineer, pid, proposal)
    worker = RoleWorker(office.scheduler, owner="fixture-worker", system_version_id=baseline, reconcile=lambda: None)
    return clock, db, pid, engineer, versions, proposal, office, secretary, gateway, handler, worker


def publish(flow, tmp_path, *, invalid=False):
    _, _, pid, engineer, _, proposal, *_ = flow
    policy = {**POLICY, "max_general_lessons": 0} if invalid else POLICY
    return engineer.implement(pid, proposal.record_id,
                              {"artifacts/context_policy.json": json.dumps(policy)}, tmp_path / "stage")


def choose_from_context(gateway, monkeypatch, *, kind="activate", alter=None, cite_candidate=True):
    original = gateway.scripted.complete
    seen = []

    def complete(request):
        candidates = request.context["candidates"]
        assert len(candidates) == 1
        candidate = candidates[0]
        seen.append(candidate)
        refs = [candidate["candidate_id"]] if cite_candidate else [request.context["reports"][0]["report_id"]]
        gateway.scripted.outputs["leader"] = reply(refs, [{"kind": kind, "candidate_id": candidate["candidate_id"]}])
        result = original(request)
        if alter:
            alter(candidate)
        return result

    monkeypatch.setattr(gateway.scripted, "complete", complete)
    return seen


@pytest.mark.parametrize("kind", ["activate", "reject"])
def test_engineer_result_routes_into_gateway_backed_leader_review(candidate_flow, tmp_path, monkeypatch, kind):
    clock, db, pid, engineer, versions, proposal, office, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)
    assert result.state == "READY"
    seen = choose_from_context(gateway, monkeypatch, kind=kind)
    digest = secretary.process(pid)
    assert "engineer:candidate_ready" in digest["groups"]
    assert worker.run_available({"leader": handler}) == 1
    assert seen[0]["objective"] == proposal.objective
    assert seen[0]["attestation"]["attestation_id"] == result.attestation_id
    assert seen[0]["attestation"]["exit_code"] == 0
    assert seen[0]["success_criteria"] == proposal.success_criteria
    assert seen[0]["rollback_criteria"] == proposal.rollback_criteria
    assert "stdout" not in seen[0]["attestation"]
    state = db.execute("SELECT state FROM candidates WHERE candidate_id = ?", (result.candidate_id,)).fetchone()[0]
    assert state == ("OBSERVING" if kind == "activate" else "REJECTED")
    assert versions.current_hash(pid) == (result.content_hash if kind == "activate" else proposal.baseline_hash)
    decision = json.loads(db.execute("SELECT document_json FROM leader_decisions ORDER BY rowid DESC").fetchone()[0])
    assert result.candidate_id in decision["evidence_refs"]
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    # Source cursor, digest and role result survive re-instantiation with no duplicate review.
    restarted = type(secretary)(office.execution, office.scheduler)
    for _ in range(3):
        restarted.process(pid)
    assert worker.run_available({"leader": handler}) == 0
    assert len(seen) == 1


def test_failed_candidate_is_visible_and_can_be_rejected(candidate_flow, tmp_path, monkeypatch):
    _, db, pid, _, versions, proposal, _, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path, invalid=True)
    assert result.state == "FAILED"
    seen = choose_from_context(gateway, monkeypatch, kind="reject")
    digest = secretary.process(pid)
    assert "engineer:candidate_failed" in digest["groups"]
    assert worker.run_available({"leader": handler}) == 1
    assert seen[0]["attestation"]["exit_code"] != 0
    assert versions.current_hash(pid) == proposal.baseline_hash
    assert db.execute("SELECT state FROM candidates").fetchone()[0] == "REJECTED"
    assert db.execute("SELECT COUNT(*) FROM candidate_attestations").fetchone()[0] == 1


def test_candidate_report_is_sufficient_specific_evidence(candidate_flow, tmp_path, monkeypatch):
    _, db, pid, _, versions, _, _, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)
    choose_from_context(gateway, monkeypatch, cite_candidate=False)
    secretary.process(pid)
    assert worker.run_available({"leader": handler}) == 1
    assert versions.current_hash(pid) == result.content_hash


@pytest.mark.parametrize("mutation", ["state", "attestation"])
def test_candidate_must_still_match_reviewed_snapshot(candidate_flow, tmp_path, monkeypatch, mutation):
    _, db, pid, _, versions, proposal, _, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)

    def alter(candidate):
        if mutation == "state":
            db.execute("UPDATE candidates SET state = 'REJECTED' WHERE candidate_id = ?", (result.candidate_id,))
        else:
            db.execute("UPDATE candidate_attestations SET exit_code = 1 WHERE attestation_id = ?",
                       (result.attestation_id,))

    choose_from_context(gateway, monkeypatch, alter=alter)
    secretary.process(pid)
    assert worker.run_available({"leader": handler}) == 1
    output = json.loads(db.execute("SELECT document_json FROM role_results ORDER BY rowid DESC").fetchone()[0])
    assert output["_status"] == "FAILED"
    assert output["error"] == "StaleState"
    assert versions.current_hash(pid) == proposal.baseline_hash
    assert db.execute("SELECT COUNT(*) FROM leader_decisions").fetchone()[0] == 1  # Commission only.
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 2


def test_guessed_candidate_not_in_snapshot_cannot_be_activated(candidate_flow, tmp_path, monkeypatch):
    _, db, pid, _, versions, proposal, _, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)
    secretary.process(pid)
    original_context = handler.context

    def hide(task):
        context = original_context(task)
        context["candidates"] = []
        return context

    monkeypatch.setattr(handler, "context", hide)
    gateway.scripted.outputs["leader"] = reply(secretary.digest(pid)["evidence_refs"],
                                              [{"kind": "activate", "candidate_id": result.candidate_id}])
    assert worker.run_available({"leader": handler}) == 1
    output = json.loads(db.execute("SELECT document_json FROM role_results ORDER BY rowid DESC").fetchone()[0])
    assert output["_status"] == "FAILED" and output["error"] == "AuthorityDenied"
    assert versions.current_hash(pid) == proposal.baseline_hash


def test_unrelated_citation_cannot_authorize_candidate_action(candidate_flow, tmp_path, monkeypatch):
    _, db, pid, _, versions, proposal, _, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)
    other = secretary.report(pid, role="research", kind="finding", summary="Unrelated evidence",
                             evidence_refs=["unrelated-source"], source_key="unrelated")
    secretary.process(pid)
    gateway.scripted.outputs["leader"] = reply([other], [{"kind": "activate", "candidate_id": result.candidate_id}])
    assert worker.run_available({"leader": handler}) == 1
    output = json.loads(db.execute("SELECT document_json FROM role_results ORDER BY rowid DESC").fetchone()[0])
    assert output["_status"] == "FAILED" and output["error"] == "ValidationFailure"
    assert versions.current_hash(pid) == proposal.baseline_hash


def test_foreign_candidate_event_never_enters_another_portfolios_context(candidate_flow, tmp_path):
    _, db, pid, engineer, _, _, office, secretary, gateway, handler, _ = candidate_flow
    result = publish(candidate_flow, tmp_path)
    other = engineer.ledger.create_portfolio(reporting_currency="EUR")
    engineer.ledger._activity(other, "engineer_candidate", {"candidate_id": result.candidate_id, "state": "READY"})
    secretary.process(other)
    assert db.execute("SELECT COUNT(*) FROM secretary_reports WHERE portfolio_id = ?", (other,)).fetchone()[0] == 0
    assert secretary.review_candidates(other) == []
    assert secretary.review_candidates(pid)[0]["candidate_id"] == result.candidate_id


def test_activation_effect_recovers_after_crash_without_another_review(candidate_flow, tmp_path, monkeypatch):
    clock, db, pid, _, versions, _, office, secretary, gateway, handler, worker = candidate_flow
    result = publish(candidate_flow, tmp_path)
    seen = choose_from_context(gateway, monkeypatch)
    secretary.process(pid)
    finish = office.scheduler.finish

    def crash(*args, **kwargs):
        raise RuntimeError("lost worker finish after committed activation")

    monkeypatch.setattr(office.scheduler, "finish", crash)
    with pytest.raises(RuntimeError):
        worker.run_available({"leader": handler})
    assert versions.current_hash(pid) == result.content_hash
    clock.advance(31)
    monkeypatch.setattr(office.scheduler, "finish", finish)
    assert worker.run_available({"leader": handler}) == 1
    assert len(seen) == 1
    assert db.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'activate'").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 2
