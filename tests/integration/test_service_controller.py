"""Owner controls exercise real lifecycle fences and SQLite, without credentials."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_execution import _decision, _quote, _stack

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.paper_service import PaperService
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.service_controller import ServiceController, process_identity
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


def runtime_stack(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    runtime = SimpleNamespace(
        database=ledger.database,
        clock=clock,
        ledger=ledger,
        execution=execution,
        portfolio_id=portfolio,
        deployment_id="deployment",
    )
    return runtime, broker


def synthetic_ready():
    return {
        "paper_available": True,
        "live_available": False,
        "ai_available": True,
        "reasons": [],
        "live_reasons": ["Protected commissioning missing"],
    }


def test_concurrent_start_and_replay_attach_to_one_launch(tmp_path):
    runtime, broker = runtime_stack(tmp_path)
    launches = []

    def launch(command):
        launches.append(command)
        return SimpleNamespace(pid=os.getpid())

    control = ServiceController(runtime, launcher=launch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: control.start_trading(f"request-{index}"), range(4)))
    assert len(launches) == 1
    assert sum(not result["attached"] for result in results) == 1
    assert len({result["run_id"] for result in results}) == 1
    assert control.start_trading("request-0")["replayed"]
    with pytest.raises(ValidationFailure, match="another command"):
        control.start_trading("request-0", "live")
    assert broker.submit_count == 0
    assert "--service-run-id" in launches[0]
    assert control.status()["prerequisites"]["ai_available"] is False


def test_interrupted_start_is_visible_and_new_owner_request_recovers(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    db = runtime.database
    db.execute(
        """INSERT INTO graph_service_runs
        (run_id, portfolio_id, mode, status, pid, pid_start_ticks, requested_at)
        VALUES ('dead', ?, 'paper', 'RUNNING', ?, 'wrong-birth', ?)""",
        (runtime.portfolio_id, os.getpid(), utc_iso(runtime.clock.now())),
    )
    launches = []
    control = ServiceController(
        runtime, launcher=lambda command: launches.append(command) or SimpleNamespace(pid=os.getpid())
    )
    assert control.status()["service"]["status"] == "INTERRUPTED"
    result = control.start_trading("recover")
    assert not result["attached"]
    assert len(launches) == 1
    assert db.execute("SELECT status FROM graph_service_runs WHERE run_id='dead'").fetchone()[0] == "INTERRUPTED"


def test_launch_without_pid_has_bounded_recovery_and_launch_failure_is_retained(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    runtime.database.execute(
        """INSERT INTO graph_service_runs
        (run_id, portfolio_id, mode, status, requested_at)
        VALUES ('lost-launch', ?, 'paper', 'STARTING', ?)""",
        (runtime.portfolio_id, utc_iso(runtime.clock.now() - timedelta(seconds=31))),
    )

    def failed(command):
        raise OSError("private host path or credentials must never be projected")

    control = ServiceController(runtime, launcher=failed)
    assert control.status()["service"]["error_type"] == "LaunchInterrupted"
    result = control.start_trading("new-start")
    assert result["lifecycle"]["service"]["status"] == "FAILED"
    assert result["lifecycle"]["service"]["error_type"] == "OSError"
    assert "credentials" not in json.dumps(result)


def test_owner_authentication_csrf_validation_and_live_fail_closed(tmp_path):
    runtime, broker = runtime_stack(tmp_path)
    launched = []
    runtime.service_controller = ServiceController(
        runtime, launcher=lambda command: launched.append(command) or SimpleNamespace(pid=os.getpid())
    )
    client = TestClient(create_app(runtime))
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    reader, _ = issue_session(runtime.database, runtime.clock, "reader")
    assert client.get("/api/v1/service").status_code == 401
    assert (
        client.post(
            "/api/v1/owner/start-trading", headers={"Authorization": f"Bearer {reader}"}, json={"request_id": "denied"}
        ).status_code
        == 403
    )
    client.cookies.set("tg_session", token)
    assert client.post("/api/v1/owner/start-trading", json={"request_id": "csrf"}).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert (
        client.post(
            "/api/v1/owner/start-trading", headers=headers, json={"request_id": "live", "mode": "live"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/owner/start-trading", headers=headers, json={"request_id": "bad", "shell": "bad"}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/owner/start-trading", headers=headers, json={"request_id": "paper"}).status_code == 202
    assert len(launched) == 1 and broker.submit_count == 0
    page = client.get("/progress")
    assert page.status_code == 200
    assert "Start Trading" in page.text and "Start Optimisation" in page.text
    assert "Automatic scheduling is disabled" in page.text


def test_paid_api_configuration_is_refused_and_default_is_management_only(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    config = tmp_path / "private-config.json"
    config.write_text(json.dumps({"models": {"paid_calls_enabled": True}}))
    config.chmod(0o600)
    control = ServiceController(runtime, config_path=config, launcher=lambda command: pytest.fail("must not launch"))
    with pytest.raises(AuthorityDenied, match="Direct paid API"):
        control.start_trading("api-refused")
    assert ServiceController(runtime).status()["prerequisites"]["ai_available"] is False
    with pytest.raises(AuthorityDenied, match="isolated subscription"):
        ServiceController(runtime).start_optimisation("no-inference")


def test_manual_cycle_is_one_shot_deduplicated_and_concurrent_cycle_explicit(tmp_path):
    runtime, broker = runtime_stack(tmp_path)
    seen = []
    service = PaperService(
        runtime.database,
        runtime.execution,
        handlers={"optimisation": lambda task: seen.append(task) or {}},
        schedule_intervals={"optimisation": 1},
    )
    control = ServiceController(runtime, prerequisites=synthetic_ready)

    async def scenario():
        await service.start()
        first = control.start_optimisation("one-cycle")
        replay = control.start_optimisation("one-cycle")
        assert replay["task_id"] == first["task_id"] and replay["replayed"]
        with pytest.raises(StaleState, match="already active"):
            control.start_optimisation("concurrent-cycle")
        result = await service.tick(wait_roles=True)
        assert result.completed == 1 and result.scheduled == 0
        assert control.status()["optimisation"]["status"] == "SUCCEEDED"
        runtime.clock.advance(604800 * 3)
        assert (await service.tick(wait_roles=True)).scheduled == 0
        assert len(seen) == 1
        task = runtime.database.execute("SELECT * FROM tasks WHERE task_id = ?", (first["task_id"],)).fetchone()
        assert task["max_attempts"] == 1 and task["deadline_at"]
        await service.stop()
        assert control.status()["service"]["status"] == "STOPPED"

    asyncio.run(scenario())
    assert broker.submit_count == 0


def test_previous_auto_cycle_retired_and_leader_cannot_recommission(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    scheduler = Scheduler(runtime.database, runtime.clock)
    old = scheduler.add_task(
        role="optimisation", objective="artifact-optimisation-review", portfolio_id=runtime.portfolio_id
    )
    scheduler.ensure_schedule(runtime.portfolio_id, "artifact-optimisation-review", 60, "coalesce")
    seen = []
    service = PaperService(
        runtime.database,
        runtime.execution,
        handlers={"optimisation": lambda task: seen.append(task) or {}},
        schedule_intervals={"optimisation": 1},
    )

    async def scenario():
        assert (await service.tick(wait_roles=True)).completed == 0
        assert (
            runtime.database.execute("SELECT status FROM tasks WHERE task_id = ?", (old,)).fetchone()[0] == "CANCELLED"
        )
        assert (
            runtime.database.execute("SELECT COUNT(*) FROM schedules WHERE name LIKE '%optimisation%'").fetchone()[0]
            == 0
        )
        with pytest.raises(AuthorityDenied, match="owner-requested"):
            service.scheduler.add_task(
                role="optimisation", objective="Leader commission", portfolio_id=runtime.portfolio_id
            )
        await service.stop()

    asyncio.run(scenario())
    assert seen == []


def test_owner_cycle_descendants_share_deadline_and_one_attempt(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    scheduler = Scheduler(runtime.database, runtime.clock)
    deadline = utc_iso(runtime.clock.now() + timedelta(minutes=10))
    root = scheduler.add_task(
        role="optimisation",
        objective="owner-optimisation-cycle",
        portfolio_id=runtime.portfolio_id,
        max_attempts=1,
        deadline_at=deadline,
    )
    child = scheduler.add_task(
        role="leader", objective="Review once", portfolio_id=runtime.portfolio_id, parent_id=root, max_attempts=3
    )
    row = runtime.database.execute("SELECT * FROM tasks WHERE task_id = ?", (child,)).fetchone()
    assert row["max_attempts"] == 1 and row["deadline_at"] == deadline


def test_quota_exhaustion_pauses_all_new_ai_while_management_continues(tmp_path):
    runtime, broker = runtime_stack(tmp_path)
    runtime.database.execute(
        "INSERT INTO subscription_provider_state VALUES ('codex', 1, 'quota exhausted', '{}', ?)",
        (utc_iso(runtime.clock.now()),),
    )
    calls = []
    service = PaperService(
        runtime.database,
        runtime.execution,
        handlers={"research": lambda task: calls.append(task) or {}},
        schedule_intervals={"research": 1},
    )
    reconciliations = []
    original = runtime.execution.reconcile

    async def reconcile():
        reconciliations.append(True)
        return await original()

    runtime.execution.reconcile = reconcile

    async def scenario():
        result = await service.tick(wait_roles=True)
        assert result.scheduled == result.completed == 0
        assert not calls and len(reconciliations) >= 2
        control = ServiceController(runtime, prerequisites=synthetic_ready)
        assert not control.status()["prerequisites"]["ai_available"]
        assert control.status()["ai_usage"]["providers"][0]["ai_paused"]
        with pytest.raises(AuthorityDenied):
            control.start_optimisation("exhausted")
        await service.stop()

    asyncio.run(scenario())
    assert broker.submit_count == 0


def test_nonflat_stop_keeps_worker_and_management_flat_stop_drains(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    runtime.execution.save_observation(_quote(runtime.clock, "99", "100"))
    intent = runtime.execution.authorize(runtime.portfolio_id, _decision(runtime.clock, runtime.portfolio_id))
    control = ServiceController(runtime)
    service = PaperService(runtime.database, runtime.execution, schedule_intervals={})

    async def scenario():
        await service.start()
        stopped = control.stop("manage-nonflat")
        assert stopped["profile"] == "MANAGE_ONLY" and not stopped["service_stop_requested"]
        assert runtime.database.execute("SELECT stop_requested FROM graph_service_runs").fetchone()[0] == 0
        assert (await service.tick()).management
        assert runtime.execution.intent_state(intent) != "UNKNOWN"
        await service.stop()

    asyncio.run(scenario())


def test_flat_stop_request_observed_by_heartbeat(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    control = ServiceController(runtime)
    service = PaperService(runtime.database, runtime.execution, schedule_intervals={})

    async def scenario():
        await service.start()
        result = control.stop("flat-stop")
        assert result["service_stop_requested"] and result["profile"] == "STOPPED"
        service._heartbeat_once()
        assert service._stop_requested.is_set()
        await service.stop()
        row = runtime.database.execute("SELECT * FROM graph_service_runs").fetchone()
        assert row["status"] == "STOPPED" and row["pid_start_ticks"] == process_identity(os.getpid())

    asyncio.run(scenario())
