"""Scripted owner resume mechanics; no live commissioning or Kraken requests."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_execution_reconciliation_ordering import _intent
from tests.integration.test_live_pilot import fixture as fixture
from tests.integration.test_live_startup_service import scripted_service

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.controls import Command, _Commands, _revision, _scope
from trade_graph.application.ledger import Ledger
from trade_graph.application.service_controller import ServiceController
from trade_graph.contracts.models import BalanceSnapshot
from trade_graph.domain.errors import AuthorityDenied, StaleState


def resumed_stack(fixture, monkeypatch):
    runtime, broker, service = scripted_service(fixture)
    runtime.config = runtime.config.model_copy(update={"scope": fixture.scope})
    runtime.deployment_id = fixture.scope.deployment_id
    # A synthetic broker exercising the production checks. This string and the
    # readiness monkeypatch never constitute actual commissioning evidence.
    broker.transport = SimpleNamespace(observation_basis="owned_https")
    monkeypatch.setattr(runtime.lifecycle, "_ready", lambda _row, _grant: True)
    controller = ServiceController(runtime)
    runtime.service_controller = controller
    return runtime, broker, service, controller


def seed_owner_pause(fixture, runtime):
    fixture.db.execute("UPDATE live_pilot_grants SET state='ACTIVE', generation=3")
    runtime.execution.set_pause(fixture.pid, "MANAGE_ONLY", "owner", "synthetic owner pause")


def saved(fixture, request_id):
    row = fixture.db.execute("SELECT result_json FROM service_control_requests WHERE request_id=?",
                             (request_id,)).fetchone()
    return json.loads(row[0])


def test_owner_resume_queues_without_native_call_then_exactly_once_effect(fixture, monkeypatch):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        calls = list(broker.calls)
        queued = controller.resume_live("resume-once")
        assert queued["status"] == "QUEUED"
        assert broker.calls == calls
        duplicate = controller.resume_live("resume-once")
        assert duplicate["replayed"] and duplicate["revision"] == queued["revision"]
        assert runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        with pytest.raises(StaleState, match="already"):
            controller.resume_live("second-request")
        await service.tick()
        assert saved(fixture, "resume-once")["status"] == "SUCCEEDED"
        assert runtime.execution.profile(fixture.pid) == "RUNNING"
        replay = controller.resume_live("resume-once")
        assert replay["replayed"] and replay["status"] == "SUCCEEDED"
        assert controller.status()["live_resume"]["status"] == "SUCCEEDED"
        before = len(fixture.db.execute("SELECT * FROM dashboard_commands").fetchall())
        await service.tick()
        assert len(fixture.db.execute("SELECT * FROM dashboard_commands").fetchall()) == before
        assert fixture.db.execute(
            "SELECT count(*) FROM activity_events WHERE kind='live_owner_resumed'"
        ).fetchone()[0] == 1
        await service.stop()

    asyncio.run(scenario())
    assert not any(kind == "submit" for kind, _ in broker.calls)


@pytest.mark.parametrize("barrier", ["unknown", "billing", "account", "recovery", "expired", "revoked"])
def test_current_native_or_authority_barrier_fails_and_preserves_pause(fixture, monkeypatch, barrier):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        if barrier == "expired":
            fixture.clock.advance(86390)
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("blocked")
        if barrier == "unknown":
            _intent(fixture.db, fixture.clock, fixture.pid, "synthetic-unknown", status="UNKNOWN",
                    account=fixture.scope.account_id, venue=fixture.scope.venue, mode="live")
        elif barrier == "billing":
            fixture.db.execute("""INSERT INTO budget_reservations
                (reservation_id,deployment_id,task_id,root_task_id,role,currency,amount,state,
                 price_card_id,purpose,synthetic,created_at,updated_at)
                VALUES ('synthetic-uncertain','deployment','task','task','research','EUR','1','UNCERTAIN',
                        'synthetic','test',0,?,?)""",
                (runtime.execution.now(), runtime.execution.now()))
        elif barrier == "account":
            async def mismatch():
                return BalanceSnapshot(venue=fixture.scope.venue, account_id=fixture.scope.account_id,
                                       as_of_utc=fixture.clock.now(), amounts={"USD": "1"})
            broker.balances = mismatch
        elif barrier in {"recovery", "revoked"}:
            fixture.db.execute("UPDATE live_pilot_grants SET state=?",
                               ("RECOVERY_REQUIRED" if barrier == "recovery" else "REVOKED",))
        else:
            fixture.clock.advance(11)
        await service.tick()
        assert saved(fixture, "blocked")["status"] == "FAILED"
        expected = {
            "unknown": "unresolved orders", "billing": "unresolved billing", "account": "reconciliation",
            "recovery": "not ACTIVE", "expired": "freshness", "revoked": "not ACTIVE",
        }
        assert expected[barrier] in saved(fixture, "blocked")["reason"]
        assert runtime.execution.profile(fixture.pid) != "RUNNING"
        assert controller.resume_live("blocked")["status"] == "FAILED"
        await service.stop()

    asyncio.run(scenario())
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_new_owner_stop_fences_pending_resume(fixture, monkeypatch):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("older-resume")
        controller.stop("newer-stop")
        assert saved(fixture, "older-resume")["status"] == "CANCELLED"
        await service.tick()
        assert saved(fixture, "older-resume")["status"] == "CANCELLED"
        assert runtime.execution.profile(fixture.pid) == "STOPPED"
        await service.stop()

    asyncio.run(scenario())
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_new_system_flatten_cannot_be_lifted(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("no-system-lift")
        # The protected controller can latch independently of HTTP revision.
        fixture.db.execute("UPDATE pause_states SET profile='FLATTEN', originator='system'")
        await service.tick()
        assert saved(fixture, "no-system-lift")["status"] == "FAILED"
        assert runtime.execution.profile(fixture.pid) == "FLATTEN"
        await service.stop()

    asyncio.run(scenario())


def test_deadline_and_interruption_never_replay_resume(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("expired-request")
        fixture.clock.advance(121)
        await service.tick()
        assert saved(fixture, "expired-request")["status"] == "FAILED"
        assert "expired" in saved(fixture, "expired-request")["reason"]
        controller.resume_live("old-run")
        await service.stop()
        await service.start()
        await service.tick()
        assert saved(fixture, "old-run")["status"] == "CANCELLED"
        assert runtime.execution.profile(fixture.pid) != "RUNNING"
        await service.stop()

    asyncio.run(scenario())


def test_live_resume_api_owner_only_and_locally_queued(fixture, monkeypatch):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)
    client = TestClient(create_app(runtime))
    owner, csrf = issue_session(fixture.db, fixture.clock, "owner")
    reader, _ = issue_session(fixture.db, fixture.clock, "reader")

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        calls = list(broker.calls)
        assert client.post("/api/v1/owner/resume",
                           headers={"Authorization": f"Bearer {reader}"}, json={}).status_code == 403
        client.cookies.set("tg_session", owner)
        assert client.post("/api/v1/owner/resume", json={}).status_code == 403
        scope = _scope(runtime, "owner")
        response = client.post("/api/v1/owner/resume", headers={"X-CSRF-Token": csrf},
                               json={"request_id": "http-resume", "expected_revision": _revision(runtime, scope)})
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"
        assert broker.calls == calls and runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        await service.tick()
        assert controller.status()["live_resume"]["status"] == "SUCCEEDED"
        await service.stop()

    asyncio.run(scenario())


def test_readiness_failure_rolls_back_provisional_running(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)
    monkeypatch.setattr(runtime.lifecycle, "_ready", lambda _row, _grant: False)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("no-readiness")
        await service.tick()
        assert saved(fixture, "no-readiness")["status"] == "FAILED"
        assert runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        await service.stop()

    asyncio.run(scenario())


def test_missing_service_and_stale_owner_revision_cannot_queue(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)
    seed_owner_pause(fixture, runtime)
    with pytest.raises(AuthorityDenied, match="active"):
        controller.resume_live("offline")

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        scope = _scope(runtime, "owner")
        _Commands(runtime).mutate(scope, Command(), "other", lambda: {})
        with pytest.raises(Exception, match="409"):
            controller.resume_live("stale", expected_revision=0)
        await service.stop()

    asyncio.run(scenario())


def test_process_interruption_rolls_back_effect_and_successor_cancels_request(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)

    class InterruptedProcess(BaseException):
        pass

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("interrupted-effect")
        original = service._finish_owner_resume

        def terminate(*_args, **_kwargs):
            raise InterruptedProcess()

        monkeypatch.setattr(service, "_finish_owner_resume", terminate)
        with pytest.raises(InterruptedProcess):
            service._process_owner_resumes([])
        assert runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        assert saved(fixture, "interrupted-effect")["status"] == "QUEUED"
        monkeypatch.setattr(service, "_finish_owner_resume", original)
        # Represent the independently fenced successor's distinct durable run.
        pending = saved(fixture, "interrupted-effect")
        pending["run_id"] = "terminated-predecessor"
        fixture.db.execute("UPDATE service_control_requests SET result_json=? WHERE request_id='interrupted-effect'",
                           (json.dumps(pending),))
        service._process_owner_resumes([])
        assert saved(fixture, "interrupted-effect")["status"] == "CANCELLED"
        assert runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        await service.stop()

    asyncio.run(scenario())


def test_explicit_scope_cannot_resume_when_another_portfolio_is_added(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        Ledger(fixture.db, fixture.clock).create_portfolio(reporting_currency="EUR", mode="live")
        with pytest.raises(AuthorityDenied, match="one dedicated"):
            controller.resume_live("ambiguous-scope")
        assert fixture.db.execute("SELECT count(*) FROM service_control_requests").fetchone()[0] == 0
        await service.stop()

    asyncio.run(scenario())


def test_unverified_transport_cannot_resume_even_if_ready_flag_is_true(fixture, monkeypatch):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)
    broker.transport.observation_basis = "synthetic"

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        controller.resume_live("synthetic-refused")
        await service.tick()
        assert saved(fixture, "synthetic-refused")["status"] == "FAILED"
        assert "actual full-account" in saved(fixture, "synthetic-refused")["reason"]
        assert runtime.execution.profile(fixture.pid) == "MANAGE_ONLY"
        await service.stop()

    asyncio.run(scenario())


def test_successful_resume_restores_ai_lifecycle_and_manual_cycle_after_nonflat_stop(fixture, monkeypatch):
    runtime, broker, service, controller = resumed_stack(fixture, monkeypatch)
    runtime.prepare_runtime = lambda: {"research": lambda _task: {}}
    service.prepare_runtime = runtime.prepare_runtime
    runtime.runtime_ready = lambda: True
    service.schedule_intervals = {}
    controller.prerequisites = lambda: {
        "live_available": True, "paper_available": False, "ai_available": True,
        "reasons": [], "live_reasons": [],
    }

    async def funded_account():
        return BalanceSnapshot(venue=fixture.scope.venue, account_id=fixture.scope.account_id,
                               as_of_utc=fixture.clock.now(), amounts={"BTC": "1"})

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        runtime.ledger.deposit(fixture.pid, "BTC", Decimal("1"), "synthetic-nonflat-holdings")
        broker.balances = funded_account
        stopped = controller.stop("nonflat-stop")
        assert not stopped["service_stop_requested"]
        assert controller.status()["service"]["status"] == "MANAGEMENT_ONLY"
        controller.resume_live("resume-ai")
        await service.tick()
        assert controller.status()["service"]["status"] == "RUNNING"
        assert controller.status()["live_resume"]["status"] == "SUCCEEDED"
        cycle = controller.start_optimisation("manual-after-resume")
        assert cycle["optimisation"]["status"] == "QUEUED"
        await service.stop()

    asyncio.run(scenario())
    assert not any(kind == "submit" for kind, _ in broker.calls)


def test_resume_keeps_management_lifecycle_when_subscription_quota_is_paused(fixture, monkeypatch):
    runtime, _broker, service, controller = resumed_stack(fixture, monkeypatch)
    service.prepare_runtime = lambda: {"research": lambda _task: {}}
    runtime.runtime_ready = lambda: True

    async def scenario():
        await service.start()
        seed_owner_pause(fixture, runtime)
        fixture.db.execute("INSERT INTO subscription_provider_state VALUES ('codex',1,'quota exhausted','{}',?)",
                           (runtime.execution.now(),))
        controller.resume_live("no-ai-quota")
        await service.tick()
        assert controller.status()["live_resume"]["status"] == "SUCCEEDED"
        assert controller.status()["service"]["status"] == "MANAGEMENT_ONLY"
        assert not controller.status()["prerequisites"]["ai_available"]
        await service.stop()

    asyncio.run(scenario())
