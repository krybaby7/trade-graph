"""Bounded smoke tests use synthetic CLI bytes and real confined role handlers."""

from types import SimpleNamespace

import pytest
from tests.integration.test_subscription_departments import flow

from trade_graph.application.subscription_profile import SubscriptionAdmission
from trade_graph.contracts.models import Observation
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.paper_runtime import PaperRuntimeConfig


@pytest.fixture
def smoke_flow(tmp_path, monkeypatch):
    from trade_graph.application import subscription_smoke as smoke
    setup, protected, cli = flow(tmp_path)
    setup.db.execute("DELETE FROM role_allocations")
    setup.db.execute("DELETE FROM deployment_budget")
    admission = SubscriptionAdmission(setup.assembly.router.config, setup.assembly.router.adapter,
        {"ready": True, "selected_provider": "claude_subscription", "quota": {}}, "1" * 64)
    runtime = SimpleNamespace(database=setup.db, clock=setup.clock, portfolio_id=setup.pid,
        ledger=setup.office.execution.ledger, execution=setup.office.execution,
        artifact_runtime=setup.runtime, scheduler=setup.office.scheduler, secretary=setup.secretary,
        office=setup.office, engineer=setup.engineer, handlers=setup.assembly.handlers,
        model_handlers=setup.assembly, subscription_admission=admission,
        subscription_provider="claude_subscription", public_feed=None,
        config=PaperRuntimeConfig(), paid_calls_enabled=False, live_enabled=False,
        prepare_runtime=lambda: setup.assembly.handlers,
        runtime_ready=lambda: setup.assembly.router.readiness(setup.pid)["ready"])
    monkeypatch.setattr(smoke, "assert_boot_environment", lambda: protected.financial.manifest)
    monkeypatch.setattr(smoke, "load_subscription_profile", lambda _owner: admission)
    monkeypatch.setattr(smoke, "subscription_profile_unchanged", lambda *_args: True)
    monkeypatch.setattr(smoke, "read_owner_file", lambda _owner, name, _limit: b"{}")
    monkeypatch.setattr(smoke, "assemble_paper_runtime", lambda *_args, **_kwargs: runtime)
    closed = setup.db.close
    monkeypatch.setattr(setup.db, "close", lambda: None)
    setup.office.execution.save_observation(Observation(observation_id="public-smoke-observation",
        venue="paper", symbol="BTC/USD", event_time_utc=setup.clock.now(), available_at_utc=setup.clock.now(),
        bid="99", ask="100", volume="1", kind="quote",
        source="kraken_public_rest:paper_reference:receipt_time"))
    yield SimpleNamespace(smoke=smoke, setup=setup, protected=protected, cli=cli, runtime=runtime,
                          admission=admission, path=setup.db.path, owner=tmp_path / "owner")
    closed()


def run(harness, phase="research"):
    return harness.smoke.run_subscription_smoke(harness.path, harness.owner, phase=phase)


def test_public_research_diagnostic_has_one_real_adapter_receipt_without_financial_effects(smoke_flow):
    h = smoke_flow
    initial_pause = h.setup.office.execution.pause(h.setup.pid)
    result = run(h)
    assert result["status"] == "SUCCEEDED" and result["phase"] == "research"
    assert len(h.cli.requests) == 1 and h.cli.requests[0].role == "research"
    request = h.cli.requests[0]
    assert request.timeout_seconds <= 120 and request.max_output_tokens <= 4096
    assert request.max_tool_calls == 0 and not request.synthetic
    assert set(request.context) <= {"objective", "as_of", "market", "strategy_templates", "active_strategy_templates",
                                    "sources", "evidence_refs", "analysis_instruction", "source_instruction"}
    assert "portfolio" not in request.context and "guard" not in request.context and "reports" not in request.context
    assert all(source["kind"] in {"historical_features", "market_observation"} for source in request.context["sources"])
    assert h.setup.office.execution.pause(h.setup.pid) == initial_pause
    assert h.setup.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert h.setup.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert h.setup.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    row = h.setup.db.execute("SELECT * FROM subscription_invocations").fetchone()
    assert row["state"] == "COMPLETED" and row["actual_model"] == "claude-sonnet-5-5"
    assert row["cost_status"] == "unknown" and row["actual_cost_native"] is None
    assert result["actual_cost_native"] is None and result["cost_status"] == "unknown"


def test_cycle_requires_the_prior_successful_exact_profile_diagnostic(smoke_flow):
    h = smoke_flow
    with pytest.raises(AuthorityDenied, match="diagnostic"):
        run(h, "cycle")
    assert not h.cli.requests
    assert h.setup.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers(smoke_flow):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    queued = {role: h.setup.add(role) for role in ("research", "trader", "leader", "learning")}
    before_budget = [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]
    h.cli.outputs["research"]["findings"] = [{"source_ref": "public-smoke-observation", "question": "Spread?",
        "claim": "The public quote has a one USD spread.", "counterevidence": "One quote does not establish an edge.",
        "invalidation": "New quote.", "expires_after_seconds": 3600}]
    result = run(h, "cycle")
    assert result["status"] == "SUCCEEDED"
    assert [request.role for request in h.cli.requests] == ["research", "research", "trader"]
    assert all(request.timeout_seconds <= 120 and request.max_output_tokens <= 4096 for request in h.cli.requests)
    assert all(h.setup.row(task_id)["status"] == "QUEUED" for task_id in queued.values())
    assert h.setup.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert h.setup.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    assert h.setup.db.execute(
        "SELECT COUNT(*) FROM protected_rpc_requests "
        "WHERE json_extract(scope_json,'$.operation')='invoke_model'").fetchone()[0] == 2
    assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"
    assert before_budget == [tuple(row) for row in h.setup.db.execute("SELECT * FROM deployment_budget")]
    assert h.setup.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    tasks = h.setup.db.execute("SELECT * FROM tasks WHERE objective LIKE 'subscription-smoke:%'").fetchall()
    assert len(tasks) == 3
    assert all(row["allocated_spend"] == "0" and row["max_attempts"] == 1 and row["attempts_used"] == 1
               for row in tasks)


def test_failed_research_diagnostic_cannot_start_cycle_or_repair(smoke_flow):
    h = smoke_flow
    h.cli.outputs["research"] = {"wrong": "synthetic invalid schema"}
    result = run(h)
    assert result["status"] == "FAILED" and len(h.cli.requests) == 1
    with pytest.raises(AuthorityDenied, match="diagnostic"):
        run(h, "cycle")
    assert len(h.cli.requests) == 1


@pytest.mark.parametrize("phase", ["research", "cycle"])
def test_uncertain_result_is_durable_and_repeated_command_never_replays(smoke_flow, phase):
    h = smoke_flow
    if phase == "cycle":
        assert run(h)["status"] == "SUCCEEDED"
    h.cli.interrupt = True
    result = run(h, phase)
    assert result["status"] == "WAITING_EXTERNAL"
    count = len(h.cli.requests)
    h.cli.interrupt = False
    recovered = run(h, phase)
    assert recovered["recovered"] and recovered["status"] == "WAITING_EXTERNAL"
    assert len(h.cli.requests) == count
    if phase == "cycle":
        assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"


def test_completed_research_and_cycle_commands_are_durable_noop_on_repeat(smoke_flow):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    assert run(h)["recovered"]
    assert len(h.cli.requests) == 1
    assert run(h, "cycle")["status"] == "SUCCEEDED"
    assert run(h, "cycle")["recovered"]
    assert len(h.cli.requests) == 3


def test_cycle_failure_latches_management_without_trader_or_application_retry(smoke_flow):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    h.cli.outputs["research"] = {"wrong": "synthetic invalid schema"}
    result = run(h, "cycle")
    assert result["status"] == "FAILED"
    assert [request.role for request in h.cli.requests] == ["research", "research"]
    assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"
    assert h.setup.db.execute("SELECT COUNT(*) FROM tasks WHERE role='trader'").fetchone()[0] == 0


def test_cycle_preserves_owner_halt_without_inference_or_task_setup(smoke_flow):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    h.setup.office.execution.set_pause(h.setup.pid, "MANAGE_ONLY", "owner", "Owner retained management.")
    pause = h.setup.office.execution.pause(h.setup.pid)
    with pytest.raises(AuthorityDenied, match="pause"):
        run(h, "cycle")
    assert len(h.cli.requests) == 1
    after = h.setup.office.execution.pause(h.setup.pid)
    assert (after["profile"], after["originator"], after["reason"]) == (
        pause["profile"], pause["originator"], pause["reason"])
    assert h.setup.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_profile_drift_before_trader_blocks_further_calls_and_keeps_management(smoke_flow, monkeypatch):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    monkeypatch.setattr(h.smoke, "subscription_profile_unchanged", lambda *_args: len(h.cli.requests) < 2)
    result = run(h, "cycle")
    assert result["status"] == "FAILED"
    assert [request.role for request in h.cli.requests] == ["research", "research"]
    assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"


def test_admission_refusal_occurs_before_opening_or_mutating_runtime(monkeypatch, tmp_path):
    from trade_graph.application import subscription_smoke as smoke
    calls = []
    monkeypatch.setattr(smoke, "assert_boot_environment", lambda: None)
    monkeypatch.setattr(smoke, "load_subscription_profile", lambda _owner: SubscriptionAdmission(
        None, None, {"ready": False, "blockers": ["synthetic host refusal"]}))
    monkeypatch.setattr(smoke, "assemble_paper_runtime", lambda *_a, **_k: calls.append("runtime"))
    with pytest.raises(AuthorityDenied, match="subscription"):
        smoke.run_subscription_smoke(tmp_path / "absent.sqlite", tmp_path / "owner")
    assert not calls and not (tmp_path / "absent.sqlite").exists()


def test_invalid_phase_never_probes_or_opens_runtime(monkeypatch, tmp_path):
    from trade_graph.application import subscription_smoke as smoke
    calls = []
    monkeypatch.setattr(smoke, "assert_boot_environment", lambda: calls.append("boot"))
    with pytest.raises(ValueError, match="phase"):
        smoke.run_subscription_smoke(tmp_path / "absent.sqlite", tmp_path / "owner", phase="leader")
    assert not calls


def test_another_service_owner_blocks_smoke_without_pause_or_attempt(smoke_flow):
    from trade_graph.application.paper_service import PaperService
    h = smoke_flow
    service = PaperService(h.setup.db, h.setup.office.execution, clock=h.setup.clock,
                           portfolio_ids=[h.setup.pid], schedule_intervals={})
    service._acquire()
    try:
        pause = h.setup.office.execution.pause(h.setup.pid)
        with pytest.raises(StaleState):
            run(h)
        assert not h.cli.requests and h.setup.office.execution.pause(h.setup.pid) == pause
    finally:
        service._release()


def test_diagnostic_strips_nonpublic_quote_and_feature_inputs(smoke_flow):
    h = smoke_flow
    h.setup.office.execution.save_observation(Observation(observation_id="synthetic-eth-quote",
        venue="paper", symbol="ETH/USD", event_time_utc=h.setup.clock.now(), available_at_utc=h.setup.clock.now(),
        bid="9999", ask="10000", kind="quote", source="synthetic"))
    assert run(h)["status"] == "SUCCEEDED"
    data = h.cli.requests[0].context["market"]["ETH/USD"]
    assert data == {"observation": None, "features": {}, "history": None, "fresh": False}
    assert "synthetic-eth-quote" not in str(h.cli.requests[0].context)


def test_diagnostic_without_public_evidence_fails_before_dispatch_and_never_retries(smoke_flow):
    h = smoke_flow
    h.setup.db.execute("DELETE FROM observations WHERE observation_id='public-smoke-observation'")
    assert run(h)["status"] == "FAILED"
    assert run(h)["recovered"]
    assert h.cli.requests == []


@pytest.mark.parametrize("dispatched", [False, True])
def test_interrupted_durable_task_never_replays_even_without_saved_outcome(smoke_flow, dispatched):
    from trade_graph.adapters.models.subscription import SubscriptionJournal
    h = smoke_flow
    task_id = h.smoke._new_task(h.runtime, h.admission.profile_sha256, "research", "research")
    if dispatched:
        from trade_graph.application.paper_service import PaperService
        service = PaperService(h.setup.db, h.setup.office.execution, clock=h.setup.clock,
            portfolio_ids=[h.setup.pid], schedule_intervals={})
        lease = h.smoke._lease_exact(h.runtime, service, task_id)
        request = h.smoke._public_request(h.runtime, h.admission, task_id, service, lease)
        SubscriptionJournal(h.setup.db, h.setup.clock).begin(
            f"research:{task_id}", request, "claude_subscription", {})
    result = run(h)
    assert result["recovered"] and result["status"] == ("WAITING_EXTERNAL" if dispatched else "FAILED")
    assert not h.cli.requests
    assert h.setup.row(task_id)["status"] == result["status"]
    assert run(h)["status"] == result["status"]


def test_controller_stop_between_roles_prevents_trader(smoke_flow, monkeypatch):
    from trade_graph.application.paper_service import PaperService
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    original = PaperService._wait_for_work
    async def request_stop_after_research(service, task, **kwargs):
        result = await original(service, task, **kwargs)
        if len(h.cli.requests) == 2:
            service.request_stop()
        return result
    monkeypatch.setattr(PaperService, "_wait_for_work", request_stop_after_research)
    result = run(h, "cycle")
    assert result["status"] == "FAILED"
    assert [request.role for request in h.cli.requests] == ["research", "research"]
    assert h.setup.office.execution.profile(h.setup.pid) == "MANAGE_ONLY"


def test_cycle_finally_preserves_new_stronger_system_pause(smoke_flow, monkeypatch):
    h = smoke_flow
    assert run(h)["status"] == "SUCCEEDED"
    original = h.cli.execute
    def stop_after_research(request, **kwargs):
        result = original(request, **kwargs)
        h.setup.office.execution.set_pause(h.setup.pid, "CANCEL_ALL", "system", "Synthetic protection stop.")
        return result
    monkeypatch.setattr(h.cli, "execute", stop_after_research)
    result = run(h, "cycle")
    assert result["status"] != "SUCCEEDED"
    pause = h.setup.office.execution.pause(h.setup.pid)
    assert pause["profile"] == "CANCEL_ALL" and pause["reason"] == "Synthetic protection stop."
    assert [request.role for request in h.cli.requests] == ["research", "research"]


def test_deterministic_management_continues_during_blocking_subscription_attempt(smoke_flow, monkeypatch):
    from threading import Event

    from trade_graph.application.paper_service import PaperService
    h = smoke_flow
    inside_attempt, managed = Event(), Event()
    original_execute, original_management = h.cli.execute, PaperService._management
    def execute(request, **kwargs):
        inside_attempt.set()
        assert managed.wait(5), "synthetic provider did not observe concurrent deterministic management"
        return original_execute(request, **kwargs)
    def management(service):
        result = original_management(service)
        if inside_attempt.is_set():
            managed.set()
        return result
    monkeypatch.setattr(h.cli, "execute", execute)
    monkeypatch.setattr(PaperService, "_management", management)
    assert run(h)["status"] == "SUCCEEDED"
    assert managed.is_set() and len(h.cli.requests) == 1


def test_public_feed_cannot_use_ambient_or_unreviewed_proxy(smoke_flow, monkeypatch):
    from trade_graph.kernel import subscription_network
    h = smoke_flow
    h.runtime.public_feed = SimpleNamespace(transport=SimpleNamespace(proxy=None, trust_env=True))
    monkeypatch.setattr(subscription_network, "load_subscription_network_profile",
        lambda _owner: SimpleNamespace(market_proxy_url="http://172.28.0.2:8081"))
    with pytest.raises(AuthorityDenied, match="market proxy"):
        run(h)
    assert h.cli.requests == []
    assert h.setup.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
