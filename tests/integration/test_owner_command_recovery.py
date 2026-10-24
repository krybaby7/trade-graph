"""Abrupt owner-command process death at real SQLite effect/receipt barriers."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from decimal import Decimal

import pytest
from fastapi import HTTPException
from tests.integration.test_dashboard_controls import config_body, task_body
from tests.integration.test_dashboard_controls import stack as controls_stack
from tests.integration.test_execution import _decision, _quote

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.controls import _Commands
from trade_graph.application.owner_commands import command_history, recover_owner_commands

CRASH = 76

# The child opens the actual database and applies actual controls. os._exit
# discards Python cleanup; WAL rollback and durable receipts decide recovery.
CHILD = r'''
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.controls import _Commands, register_controls
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock

path, portfolio, endpoint, phase, body = sys.argv[1:]
database = Database(path)
clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
ledger = Ledger(database, clock)
execution = Execution(database, ledger, clock, PaperBroker(database, clock))
runtime = SimpleNamespace(database=database, clock=clock, ledger=ledger,
                          execution=execution, portfolio_id=portfolio)
if phase == "atomic-before-commit":
    original = _Commands._save
    def save(self, *args, **kwargs):
        original(self, *args, **kwargs)
        os._exit(76)
    _Commands._save = save
elif phase == "atomic-after-commit":
    original = _Commands.mutate
    def mutate(self, *args, **kwargs):
        original(self, *args, **kwargs)
        os._exit(76)
    _Commands.mutate = mutate
elif phase in {"management-entry", "management-done"}:
    original = execution.advance_pause if endpoint.endswith("pause") else execution.reconcile
    async def interrupted(*args, **kwargs):
        if phase == "management-done":
            await original(*args, **kwargs)
        os._exit(76)
    if endpoint.endswith("pause"):
        execution.advance_pause = interrupted
    else:
        execution.reconcile = interrupted
elif phase == "checkpoint-done":
    def finish(*args, **kwargs):
        os._exit(76)
    _Commands.finish = finish
elif phase == "resume-before-commit":
    original = _Commands.finish
    def finish(self, *args, **kwargs):
        original(self, *args, **kwargs)
        os._exit(76)
    _Commands.finish = finish
elif phase == "resume-after-commit":
    original = database.immediate
    from contextlib import contextmanager
    @contextmanager
    def immediate():
        nested = getattr(database._local, "connection", None) is not None
        with original() as connection:
            yield connection
        if not nested and database.execute(
            "SELECT 1 FROM dashboard_commands WHERE status = 'COMPLETE'"
        ).fetchone():
            os._exit(76)
    database.immediate = immediate
app = FastAPI()
register_controls(app, runtime, lambda request: "leader", lambda request: "owner")
response = TestClient(app).post(endpoint, json=json.loads(body))
raise AssertionError((response.status_code, response.text))
'''


@pytest.fixture
def stack(tmp_path):
    return controls_stack.__wrapped__(tmp_path)


def crash_command(stack, endpoint, phase, body):
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(stack.runtime.database.path), stack.runtime.portfolio_id,
         endpoint, phase, json.dumps(body)], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == CRASH, result.stderr + result.stdout


def forbid_effects(runtime):
    async def forbidden(*args, **kwargs):
        pytest.fail("receipt recovery must not perform broker effects")

    runtime.execution.reconcile = forbidden
    runtime.execution.advance_pause = forbidden
    runtime.execution.broker.submit = forbidden
    runtime.execution.broker.cancel = forbidden


@pytest.mark.parametrize("phase", ["atomic-before-commit", "atomic-after-commit"])
@pytest.mark.parametrize("action", ["budgets", "config", "tasks"])
def test_financial_config_and_task_effects_recover_atomic_receipts_once(stack, action, phase):
    runtime, db = stack.runtime, stack.runtime.database
    if action == "budgets":
        body = {
            "request_id": "budget-crash", "expected_revision": 0,
            "total": "6", "period": "6", "priority_reserve": "1", "daily": "2", "root": "2",
            "roles": {"leader": "2", "research": "3"},
        }
    else:
        body = config_body() if action == "config" else task_body()
    role = "leader" if action == "tasks" else "owner"
    endpoint = f"/api/v1/{role}/{action}"
    crash_command(stack, endpoint, phase, body)
    commands = db.execute("SELECT * FROM dashboard_commands").fetchall()
    assert len(commands) == (1 if phase == "atomic-after-commit" else 0)
    if commands:
        assert commands[0]["status"] == "COMPLETE"
    assert recover_owner_commands(runtime) == []
    response = stack.client.post(endpoint, headers=getattr(stack, f"{role}_headers"), json=body)
    assert response.status_code == 200
    if commands:
        assert response.json() == json.loads(commands[0]["response_json"])
    assert db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM dashboard_command_evidence").fetchone()[0] == 1
    if action == "tasks":
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    else:
        assert db.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == 2
        if action == "budgets":
            assert Decimal(db.execute("SELECT total_allowance FROM deployment_budget").fetchone()[0]) == 6
        else:
            assert stack.authority.active_policy().maximum_gross_exposure_fraction == Decimal("0.60")
    assert stack.client.post(endpoint, headers=getattr(stack, f"{role}_headers"), json=body).json() == response.json()


@pytest.mark.parametrize("phase", ["management-entry", "management-done", "checkpoint-done"])
def test_pause_recovery_distinguishes_durable_result_from_unknown_without_repeating_effects(stack, phase):
    runtime, db = stack.runtime, stack.runtime.database
    body = {"request_id": "pause-crash", "expected_revision": 0, "profile": "PAUSE_DECISIONS",
            "reason": "private owner prose"}
    crash_command(stack, "/api/v1/owner/pause", phase, body)
    assert db.execute("SELECT status FROM dashboard_commands").fetchone()[0] == "PROCESSING"
    assert runtime.execution.profile(runtime.portfolio_id) == "PAUSE_DECISIONS"
    before = runtime.execution.pause(runtime.portfolio_id)
    forbid_effects(runtime)
    recovered = recover_owner_commands(runtime)
    assert len(recovered) == 1 and recovered[0]["replayed"] is False
    committed = phase == "checkpoint-done"
    assert recovered[0]["effect_outcome"] == ("COMMITTED" if committed else "UNKNOWN")
    assert recovered[0]["needs_review"] is not committed
    assert runtime.execution.pause(runtime.portfolio_id) == before
    assert recover_owner_commands(runtime) == []
    replay = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert replay.status_code == (200 if committed else 409)
    if not committed:
        assert replay.json()["detail"]["command_state"] == "FAILED"
        assert replay.json()["detail"]["needs_review"] is True
    config = stack.client.get("/api/v1/owner/config", headers=stack.owner_headers).json()
    assert config["revision"] == 1 and config["pending_commands"] == []
    assert config["recovered_commands"][0]["recovery"]["local_effects"]["pause"]["profile"] == "PAUSE_DECISIONS"
    history = stack.client.get("/api/v1/owner/commands", headers=stack.owner_headers)
    assert "private owner prose" not in history.text
    assert "request_hash" not in history.text and "response_json" not in history.text
    from fastapi.testclient import TestClient
    page = TestClient(create_app(runtime)).get("/owner", headers=stack.owner_headers)
    assert page.status_code == 200 and "pause-crash" in page.text
    assert ("Requires review" in page.text) is not committed
    assert "private owner prose" not in page.text
    # Recovery releases the scope; an explicit new owner command is admissible.
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers,
                                 json=config_body(expected_revision=1))
    assert response.status_code == 200


@pytest.mark.parametrize("phase", [
    "management-entry", "management-done", "resume-before-commit", "resume-after-commit",
])
def test_resume_crash_never_commits_running_without_a_completed_receipt(stack, phase):
    runtime, db = stack.runtime, stack.runtime.database
    body = {"request_id": "resume-crash", "expected_revision": 0}
    crash_command(stack, "/api/v1/owner/resume", phase, body)
    committed = phase == "resume-after-commit"
    assert runtime.execution.profile(runtime.portfolio_id) == ("RUNNING" if committed else "MANAGE_ONLY")
    assert db.execute("SELECT status FROM dashboard_commands").fetchone()[0] == (
        "COMPLETE" if committed else "PROCESSING"
    )
    forbid_effects(runtime)
    result = recover_owner_commands(runtime)
    assert len(result) == (0 if committed else 1)
    replay = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers, json=body)
    assert replay.status_code == (200 if committed else 409)
    assert runtime.execution.profile(runtime.portfolio_id) == ("RUNNING" if committed else "MANAGE_ONLY")


def test_recovery_retains_newer_emergency_latch_and_terminal_failure(stack):
    runtime = stack.runtime
    body = {"request_id": "resume-crash", "expected_revision": 0}
    crash_command(stack, "/api/v1/owner/resume", "management-entry", body)
    emergency = {"request_id": "emergency", "expected_revision": 1, "profile": "MANAGE_ONLY", "reason": "newer halt"}
    assert stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=emergency).status_code == 200
    retained = runtime.execution.pause(runtime.portfolio_id)
    recover_owner_commands(runtime)
    assert runtime.execution.pause(runtime.portfolio_id) == retained
    assert stack.client.get("/api/v1/owner/config", headers=stack.owner_headers).json()["revision"] == 2
    with pytest.raises(HTTPException) as failure:
        _Commands(runtime).finish("resume-crash", 1, {"profile": "RUNNING"})
    assert failure.value.status_code == 409
    assert runtime.execution.pause(runtime.portfolio_id) == retained


def test_recovering_completed_old_pause_receipt_retains_a_newer_emergency_profile(stack):
    runtime = stack.runtime
    body = {"request_id": "pause-crash", "expected_revision": 0, "profile": "PAUSE_DECISIONS"}
    crash_command(stack, "/api/v1/owner/pause", "checkpoint-done", body)
    emergency = {"request_id": "emergency", "expected_revision": 1, "profile": "MANAGE_ONLY"}
    result = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=emergency)
    assert result.status_code == 200
    retained = runtime.execution.pause(runtime.portfolio_id)
    forbid_effects(runtime)
    assert recover_owner_commands(runtime)[0]["effect_outcome"] == "COMMITTED"
    replay = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert replay.status_code == 200 and replay.json()["profile"] == "PAUSE_DECISIONS"
    assert runtime.execution.pause(runtime.portfolio_id) == retained


def test_legacy_recovery_is_scoped_preserves_unknown_and_owner_leader_permissions(stack):
    runtime, db = stack.runtime, stack.runtime.database
    for command_id, scope in [("legacy", "owner:deployment"), ("other-owner", "owner:other"),
                              ("other-leader", "leader:other")]:
        db.execute("INSERT INTO dashboard_commands VALUES (?, ?, 'opaque', NULL, 'PROCESSING', 'now')",
                   (command_id, scope))
    result = recover_owner_commands(runtime)
    assert len(result) == 1 and result[0]["command_id"] == "legacy"
    assert result[0]["needs_review"] is True and result[0]["local_effects"] == {}
    for command_id in ("other-owner", "other-leader"):
        row = db.execute("SELECT status FROM dashboard_commands WHERE command_id = ?", (command_id,)).fetchone()
        assert row[0] == "PROCESSING"
    history = command_history(runtime, "owner:deployment")
    assert history[0]["revision"] == 0 and history[0]["action"] == "legacy-unknown"
    assert stack.client.get("/api/v1/owner/commands").status_code == 401
    assert stack.client.get("/api/v1/owner/commands", headers=stack.leader_headers).status_code == 403
    assert stack.client.get("/api/v1/leader/commands", headers=stack.owner_headers).status_code == 403
    assert stack.client.get("/api/v1/leader/commands", headers=stack.leader_headers).json()["commands"] == []
    reader, _ = issue_session(db, runtime.clock, "reader")
    assert stack.client.get("/api/v1/owner/commands", headers={"Authorization": f"Bearer {reader}"}).status_code == 403
    for params in ["limit=0", "limit=101", "offset=-1", "offset=1000001"]:
        assert stack.client.get(f"/api/v1/owner/commands?{params}", headers=stack.owner_headers).status_code == 422


def test_recovery_transaction_failure_keeps_pending_receipt_and_original_evidence(stack):
    import sqlite3

    runtime, db = stack.runtime, stack.runtime.database
    crash_command(stack, "/api/v1/owner/resume", "management-entry",
                  {"request_id": "resume-crash", "expected_revision": 0})
    before = db.execute("SELECT effect_json FROM dashboard_command_evidence").fetchone()[0]
    db.execute("""CREATE TRIGGER recovery_storage_failure BEFORE UPDATE ON dashboard_command_evidence
        BEGIN SELECT RAISE(ABORT, 'synthetic recovery storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        recover_owner_commands(runtime)
    assert db.execute("SELECT status FROM dashboard_commands").fetchone()[0] == "PROCESSING"
    assert db.execute("SELECT effect_json FROM dashboard_command_evidence").fetchone()[0] == before
    assert stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=config_body()).status_code == 409
    db.execute("DROP TRIGGER recovery_storage_failure")
    assert recover_owner_commands(runtime)[0]["needs_review"] is True


def test_unreadable_durable_evidence_requires_review_without_inferred_or_repeated_effects(stack):
    runtime, db = stack.runtime, stack.runtime.database
    crash_command(stack, "/api/v1/owner/resume", "management-entry",
                  {"request_id": "resume-crash", "expected_revision": 0})
    db.execute("UPDATE dashboard_command_evidence SET phase = 'EFFECT_COMMITTED', effect_json = ?",
               ("unreadable private bytes",))
    retained = runtime.execution.pause(runtime.portfolio_id)
    forbid_effects(runtime)
    result = recover_owner_commands(runtime)
    assert result[0]["needs_review"] is True and result[0]["effect_outcome"] == "UNKNOWN"
    assert runtime.execution.pause(runtime.portfolio_id) == retained
    raw = json.loads(db.execute("SELECT effect_json FROM dashboard_command_evidence").fetchone()[0])
    assert raw["prior_evidence_json"] == "unreadable private bytes"
    history = stack.client.get("/api/v1/owner/commands", headers=stack.owner_headers)
    assert "unreadable private bytes" not in history.text


@pytest.mark.parametrize("phase", ["management-done", "checkpoint-done"])
def test_committed_cancellation_is_never_replayed_during_receipt_recovery(stack, phase):
    runtime, db = stack.runtime, stack.runtime.database
    runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.90"), source="synthetic FX",
                              kind="synthetic", stale=False)
    runtime.execution.save_observation(_quote(runtime.clock, "99", "100"))
    intent = runtime.execution.authorize(runtime.portfolio_id, _decision(runtime.clock, runtime.portfolio_id))
    asyncio.run(runtime.execution.dispatch())
    assert runtime.execution.intent_state(intent) == "OPEN"
    db.execute("CREATE TABLE synthetic_cancel_audit (client_order_id TEXT)")
    db.execute("""CREATE TRIGGER synthetic_cancel_effect AFTER UPDATE ON broker_orders
        WHEN NEW.status = 'cancelled'
        BEGIN INSERT INTO synthetic_cancel_audit VALUES (NEW.client_order_id); END""")
    body = {"request_id": "cancel-crash", "expected_revision": 0, "profile": "NO_NEW_EXPOSURE"}
    crash_command(stack, "/api/v1/owner/pause", phase, body)
    assert runtime.execution.intent_state(intent) == "CANCELLED"
    assert db.execute("SELECT COUNT(*) FROM synthetic_cancel_audit").fetchone()[0] == 1
    forbid_effects(runtime)
    recover_owner_commands(runtime)
    replay = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert replay.status_code == (200 if phase == "checkpoint-done" else 409)
    assert db.execute("SELECT COUNT(*) FROM synthetic_cancel_audit").fetchone()[0] == 1
    assert runtime.execution.intent_state(intent) == "CANCELLED"


def test_awaited_cancellation_cannot_relabel_newer_emergency_management_state(stack):
    runtime = stack.runtime
    runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.90"), source="synthetic FX",
                              kind="synthetic", stale=False)
    runtime.execution.save_observation(_quote(runtime.clock, "99", "100"))
    intent = runtime.execution.authorize(runtime.portfolio_id, _decision(runtime.clock, runtime.portfolio_id))
    asyncio.run(runtime.execution.dispatch())
    original = runtime.execution.broker.cancel
    retained = []

    async def delayed_cancel(request):
        emergency = {"request_id": "emergency", "expected_revision": 1, "profile": "MANAGE_ONLY"}
        response = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=emergency)
        assert response.status_code == 200
        retained.append(runtime.execution.pause(runtime.portfolio_id))
        return await original(request)

    runtime.execution.broker.cancel = delayed_cancel
    body = {"request_id": "slow-pause", "expected_revision": 0, "profile": "NO_NEW_EXPOSURE"}
    response = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert response.status_code == 409
    assert runtime.execution.pause(runtime.portfolio_id) == retained[0]
    assert retained[0]["achieved"] == "reconciliation-required"
    assert runtime.execution.intent_state(intent) == "CANCELLED"
    # A subsequent management pass still reconciles the active owner profile.
    assert asyncio.run(runtime.execution.advance_pause(runtime.portfolio_id)) == "managing"
