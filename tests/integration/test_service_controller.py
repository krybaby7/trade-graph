"""Owner controls exercise real lifecycle fences and SQLite, without credentials."""

import asyncio
import json
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
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


def test_expired_requested_cycle_performs_no_model_work(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    calls = []
    service = PaperService(
        runtime.database,
        runtime.execution,
        handlers={"optimisation": lambda task: calls.append(task) or {}},
        schedule_intervals={},
    )
    control = ServiceController(runtime, prerequisites=synthetic_ready)

    async def scenario():
        await service.start()
        result = control.start_optimisation("expired-cycle")
        runtime.clock.advance(601)
        await service.tick(wait_roles=True)
        assert calls == []
        assert control._cycle(result["task_id"])["status"] == "FAILED"
        await service.stop()

    asyncio.run(scenario())


def test_nonflat_stop_preserves_system_flatten_management(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    runtime.ledger.deposit(runtime.portfolio_id, "BTC", Decimal("0.1"), "synthetic-position")
    runtime.execution.set_pause(runtime.portfolio_id, "FLATTEN", "system", "native safety incident")
    result = ServiceController(runtime).stop("preserve-protection")
    assert not result["service_stop_requested"] and result["profile"] == "FLATTEN"
    assert runtime.execution.pause(runtime.portfolio_id)["originator"] == "system"


def test_shutdown_after_tick_exception_retains_failure_status(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    service = PaperService(runtime.database, runtime.execution, schedule_intervals={})

    async def broken(**kwargs):
        raise OSError("synthetic disk failure")

    service.tick = broken
    with pytest.raises(OSError):
        asyncio.run(service.run(max_ticks=1))
    row = runtime.database.execute("SELECT * FROM graph_service_runs").fetchone()
    assert row["status"] == "FAILED" and row["error_type"] == "OSError"
    assert runtime.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0


def wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("scripted child lifecycle did not reach its expected state")


def test_actual_cli_child_singleton_kill_and_recovery_are_durable(tmp_path):
    runtime, broker = runtime_stack(tmp_path)
    tmp_path.chmod(0o700)
    control = ServiceController(runtime)
    before = runtime.database.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
    launched_pids = []
    try:
        first = control.start_trading("actual-first")
        wait_until(lambda: control.status()["service"]["status"] == "MANAGEMENT_ONLY")
        row = runtime.database.execute(
            "SELECT * FROM graph_service_runs WHERE run_id = ?", (first["run_id"],)
        ).fetchone()
        launched_pids.append(row["pid"])
        duplicate = control.start_trading("actual-duplicate")
        assert duplicate["attached"] and duplicate["run_id"] == first["run_id"]
        assert control.status()["database_owned"]
        assert control.status()["prerequisites"]["ai_available"] is False
        os.kill(row["pid"], signal.SIGKILL)
        wait_until(lambda: control.status()["service"]["status"] == "INTERRUPTED")
        second = control.start_trading("actual-recovery")
        assert second["run_id"] != first["run_id"] and not second["attached"]
        wait_until(lambda: control.status()["service"]["status"] == "MANAGEMENT_ONLY")
        row = runtime.database.execute(
            "SELECT * FROM graph_service_runs WHERE run_id = ?", (second["run_id"],)
        ).fetchone()
        launched_pids.append(row["pid"])
        assert row["pid_start_ticks"] == process_identity(row["pid"])
        assert control.stop("actual-flat-stop")["service_stop_requested"]
        wait_until(lambda: control.status()["service"]["status"] == "STOPPED")
        wait_until(lambda: not control.status()["database_owned"])
        assert runtime.database.execute("SELECT COUNT(*) FROM graph_service_runs").fetchone()[0] == 2
        assert runtime.database.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == before
        assert runtime.database.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert runtime.database.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
        assert broker.submit_count == 0
    finally:
        for pid in launched_pids:
            if process_identity(pid):
                os.kill(pid, signal.SIGTERM)
        for child in tuple(control._children.values()):
            try:
                child.wait(timeout=5)
            except TimeoutError:
                child.kill()
                child.wait(timeout=5)


def test_artifact_schedule_cannot_restore_automatic_optimisation(tmp_path):
    from tests.integration.test_version_lifecycle import lifecycle_stack

    from trade_graph.application.artifact_runtime import ArtifactRuntime

    stack = lifecycle_stack(
        tmp_path,
        additional_files={
            "artifacts/schedules.json": json.dumps(
                {"schema_version": 1, "interval_seconds": {"optimisation": 60, "research": 120}}
            ),
        },
    )
    scheduler = Scheduler(stack.database, stack.clock)
    scheduler.ensure_schedule(stack.portfolio, "artifact-optimisation-review", 60, "coalesce")
    runtime = ArtifactRuntime(stack.versions, scheduler)
    runtime.disabled_schedule_roles = {"optimisation"}
    bundle = stack.versions.begin(stack.portfolio, "owner-schedule-test", reconcile=lambda: None)
    for _ in range(2):
        runtime.apply_schedules(stack.portfolio, bundle)
        assert runtime.coalesce_due(stack.portfolio, "optimisation") is None
        assert (
            stack.database.execute(
                "SELECT COUNT(*) FROM schedules WHERE name='artifact-optimisation-review'"
            ).fetchone()[0]
            == 0
        )
    assert (
        stack.database.execute("SELECT COUNT(*) FROM schedules WHERE name='artifact-research-review'").fetchone()[0]
        == 1
    )


def test_service_stop_fences_an_owner_resume_already_reconciling(tmp_path):
    runtime, _ = runtime_stack(tmp_path)
    client = TestClient(create_app(runtime))
    token, _ = issue_session(runtime.database, runtime.clock, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    entered, finish = threading.Event(), threading.Event()
    original = runtime.execution.reconcile

    async def slow_reconcile():
        entered.set()
        assert await asyncio.to_thread(finish.wait, 5)
        return await original()

    runtime.execution.reconcile = slow_reconcile
    with ThreadPoolExecutor(max_workers=1) as pool:
        resume = pool.submit(lambda: client.post("/api/v1/owner/resume", headers=headers, json={}))
        assert entered.wait(5)
        stop = client.post(
            "/api/v1/owner/stop-service",
            headers=headers,
            json={"request_id": "newer-stop", "position_policy": "manage-only"},
        )
        assert stop.status_code == 200 and stop.json()["profile"] == "STOPPED"
        finish.set()
        assert resume.result(timeout=5).status_code == 409
    assert runtime.execution.profile(runtime.portfolio_id) == "STOPPED"
    rows = runtime.database.execute("SELECT action FROM dashboard_command_evidence").fetchall()
    assert {row[0] for row in rows} == {"resume", "service-stop"}
