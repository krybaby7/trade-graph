"""Regression cases use genuine commissioned artifacts and test rejection atomicity."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_activation import _candidate, _ready
from tests.integration.test_engineer import POLICY, _stack, _task
from tests.leadership_support import commission

from trade_graph.adapters.persistence.db import Database
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.activation import VersionController
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError


@pytest.mark.parametrize(
    "defect",
    ["unknown_candidate", "wrong_portfolio", "rejected", "revoked", "wrong_proof_identity", "stale_task", "expired"],
)
def test_invalid_activation_leaves_all_state_unchanged(tmp_path, defect):
    db, pid, versions, baseline, result = _ready(tmp_path)
    candidate = _candidate(result, baseline)
    if defect == "unknown_candidate":
        candidate["candidate_id"] = "nonexistent"
    elif defect == "wrong_portfolio":
        pid = "other-portfolio"
        versions.ensure(pid, "v1", baseline)
    elif defect == "rejected":
        db.execute("UPDATE candidates SET state = 'REJECTED' WHERE candidate_id = ?", (result.candidate_id,))
    elif defect == "revoked":
        db.execute("UPDATE engineering_commissions SET state = 'REVOKED'")
    elif defect == "wrong_proof_identity":
        db.execute("UPDATE candidate_attestations SET candidate_id = 'another-candidate'")
    elif defect == "stale_task":
        doc = json.loads(db.execute("SELECT document_json FROM change_tasks").fetchone()[0])
        doc["objective"] = "silently changed after approval"
        db.execute("UPDATE change_tasks SET document_json = ?", (json.dumps(doc),))
    elif defect == "expired":
        versions.clock.advance(86401)
    before = list(db.connection.iterdump())
    with pytest.raises(TradeGraphError):
        versions.activate(pid, candidate)
    assert list(db.connection.iterdump()) == before


@pytest.mark.parametrize("state", ["PROPOSED", "REJECTED", "CANCELLED", "EXPIRED", "OBSERVING"])
def test_nonrunnable_change_never_stages_or_changes_state(tmp_path, state):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(pid, "v1", baseline)
    task = _task(clock, pid, baseline)
    commission(engineer, pid, task)
    db.execute("UPDATE change_tasks SET state = ?", (state,))
    before = list(db.connection.iterdump())
    with pytest.raises(AuthorityDenied):
        engineer.implement(
            pid, task.record_id, {"artifacts/context_policy.json": json.dumps(POLICY)}, tmp_path / "stage"
        )
    assert not (tmp_path / "stage").exists()
    assert list(db.connection.iterdump()) == before


def test_proposal_and_caller_role_are_not_commissions(tmp_path):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(pid, "v1", baseline)
    task = _task(clock, pid, baseline)
    with pytest.raises(AuthorityDenied):
        engineer.commission(pid, task)
    engineer.propose(pid, task)
    before = list(db.connection.iterdump())
    with pytest.raises(AuthorityDenied):
        engineer.implement(
            pid, task.record_id, {"artifacts/context_policy.json": json.dumps(POLICY)}, tmp_path / "stage"
        )
    assert list(db.connection.iterdump()) == before
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("mechanism", ["cookie", "bearer"])
def test_real_leader_activation_requires_csrf_but_authorized_access_succeeds(tmp_path, mechanism):
    db, pid, versions, baseline, result = _ready(tmp_path)
    runtime = SimpleNamespace(database=db, clock=versions.clock, portfolio_id=pid)
    client = TestClient(create_app(runtime))
    token, csrf = issue_session(db, versions.clock, "leader")
    client.cookies.set("tg_session", token)
    body = _candidate(result, baseline)
    before = list(db.connection.iterdump())
    assert client.post("/api/v1/leader/activate", json=body).status_code == 403
    assert list(db.connection.iterdump()) == before
    headers = {"X-CSRF-Token": csrf} if mechanism == "cookie" else {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/leader/activate", json=body, headers=headers).status_code == 200
    assert versions.current_hash(pid) == result.content_hash


def test_restart_and_concurrent_activation_have_one_commit(tmp_path):
    db, pid, versions, baseline, result = _ready(tmp_path)
    path, clock = db.path, versions.clock
    db.close()

    def activate():
        connection = Database(path)
        try:
            VersionController(connection, clock).activate(pid, _candidate(result, baseline))
            return "activated"
        except StaleState:
            return "stale"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: activate(), range(2))) == ["activated", "stale"]
    restarted = Database(path)
    assert restarted.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'activate'").fetchone()[0] == 1
    before = list(restarted.connection.iterdump())
    with pytest.raises(TradeGraphError):
        VersionController(restarted, clock).rollback(pid, baseline, "invented-version")
    assert list(restarted.connection.iterdump()) == before


def test_revocation_during_checks_retains_failure_not_ready(tmp_path, monkeypatch):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(pid, "v1", baseline)
    task = _task(clock, pid, baseline)
    commission(engineer, pid, task)
    attest = engineer.runner.attest

    def revoke(root):
        report = attest(root)
        db.execute("UPDATE engineering_commissions SET state = 'REVOKED'")
        return report

    monkeypatch.setattr(engineer.runner, "attest", revoke)
    result = engineer.implement(
        pid, task.record_id, {"artifacts/context_policy.json": json.dumps(POLICY)}, tmp_path / "stage"
    )
    assert result.state == "FAILED"
    assert db.execute("SELECT COUNT(*) FROM candidate_attestations").fetchone()[0] == 1
    assert versions.current_hash(pid) == baseline
