"""Synthetic CLI results exercise real confined roles; no model/network calls."""

import hashlib
import json

import pytest
from tests.integration.test_engineer import POLICY, _task
from tests.integration.test_protected_department_graph import GRAPH
from tests.integration.test_runtime_models import RuntimeFlow
from tests.leadership_support import reply

from trade_graph.adapters.models.subscription import (
    CliOutcome,
    SubscriptionAdapter,
    SubscriptionConfig,
    SubscriptionReadiness,
)
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.application.subscription_runtime import (
    ROLE_CONTEXT_KEYS,
    SubscriptionRuntimeConfig,
    assemble_subscription_handlers,
)
from trade_graph.domain.money import Money
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256


class SyntheticCli:
    def __init__(self):
        self.requests = []
        self.outputs = {}
        self.interrupt = False

    def execute(self, request, *, cancel_event=None):
        self.requests.append(request)
        if self.interrupt:
            return CliOutcome("", 137, stopped="timeout")
        payload = self.outputs[request.role]
        if "evidence_refs" in payload:
            payload = {**payload, "evidence_refs": request.context["evidence_refs"][:20]}
        return CliOutcome(json.dumps({"type": "result", "is_error": False, "num_turns": 1,
            "structured_output": payload, "modelUsage": {
                "claude-sonnet-5-5": {"inputTokens": 7, "outputTokens": 9}}}), 0)


def flow(tmp_path, *, blocked=False, source=GRAPH):
    setup = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=True, keys={})
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="deployment", approved_source_sha256=(hashlib.sha256(source.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    protected = ProtectedPaperRuntime(database=setup.db, clock=setup.clock, execution=setup.office.execution,
        manifest=manifest, capability_key=b"synthetic-parent-subscription-key-41", instance_id="subscriptions")
    protected.controller.admit_release(release_id="graph-v1", source_text=source)
    protected.controller.activate_release("graph-v1")
    provider = "codex_subscription" if blocked else "claude_subscription"
    model = "gpt-6.1-sol" if blocked else "claude-sonnet-5-5"
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(provider=provider, model=model,
        enabled=True, allowed_context_keys={role: sorted(keys) for role, keys in ROLE_CONTEXT_KEYS.items()}))
    cli = SyntheticCli()
    status = SubscriptionReadiness(provider, "0.125.0" if blocked else "2.1.285", not blocked,
        ("Codex built-in automatic retry unsupported",) if blocked else (), {},
        "chatgpt" if blocked else "subscription", "linux-bubblewrap")
    adapter = SubscriptionAdapter(config.subscription, status, cli)
    setup.assembly = assemble_subscription_handlers(setup.office, setup.secretary, setup.engineer, setup.runtime,
        config, protected_runtime=protected, workspace_root=setup.source.parent / "subscription-engineer",
        adapter=adapter)
    common = {"evidence_refs": [setup.ref], "summary": "Bounded evidence.", "outcome": "Insufficient sample."}
    cli.outputs.update({"research": {**common, "findings": []}, "learning": {**common, "lessons": []},
        "optimisation": {**common, "proposals": []}, "leader": reply([setup.ref]),
        "trader": {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
            "invalidation": "New evidence.", "experiment": False, "evidence_ids": [],
            "no_action_reason": "No signal."},
        "engineer": {"summary": "Reduce retained general context.", "files": [
            {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}})
    return setup, protected, cli


def test_zero_money_subscription_allocation_needs_no_api_funding(tmp_path):
    from decimal import Decimal

    setup, _protected, cli = flow(tmp_path)
    setup.db.execute('DELETE FROM role_allocations')
    setup.db.execute('DELETE FROM deployment_budget')
    task = setup.office.scheduler.add_task(role='research', objective='Review current public evidence.',
        portfolio_id=setup.pid, max_attempts=1, allocated_spend=Decimal('0'))
    assert setup.run('research') == 1
    assert setup.row(task)['status'] == 'SUCCEEDED', setup.row(task)['output_json']
    assert len(cli.requests) == 1
    assert cli.requests[0].timeout_seconds == setup.assembly.router.config.subscription.maximum_seconds
    assert cli.requests[0].max_output_tokens == setup.assembly.router.config.subscription.maximum_output_tokens
    assert setup.db.execute('SELECT COUNT(*) FROM deployment_budget').fetchone()[0] == 0
    receipt = setup.db.execute('SELECT * FROM subscription_invocations WHERE task_id=?', (task,)).fetchone()
    assert receipt['cost_status'] == 'unknown' and receipt['actual_cost_native'] is None


def test_nonzero_subscription_allocation_cannot_invent_missing_real_budget(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    setup.db.execute('DELETE FROM role_allocations')
    setup.db.execute('DELETE FROM deployment_budget')
    task = setup.add('research')
    assert setup.run('research') == 1
    assert setup.row(task)['status'] != 'SUCCEEDED'
    assert cli.requests == []


def test_all_six_subscription_roles_use_confined_rpc_and_separate_unknown_cost_receipts(tmp_path):
    setup, protected, cli = flow(tmp_path)
    for role in ("research", "trader", "learning", "optimisation"):
        identity = setup.add(role)
        assert setup.run(role) == 1
        assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    change = _task(setup.clock, setup.pid, setup.baseline, max_steps=1,
                   max_spend=Money(amount="0.5", currency="EUR"))
    setup.engineer.propose(setup.pid, change)
    cli.outputs["leader"] = reply([setup.ref, change.record_id], [
        {"kind": "commission", "change_id": change.record_id}])
    identity = setup.add("leader")
    assert setup.run("leader") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    assert setup.run("engineer") == 1
    engineer = setup.db.execute("SELECT * FROM tasks WHERE role='engineer'").fetchone()
    assert engineer["status"] == "SUCCEEDED", engineer["output_json"]
    rows = setup.db.execute("SELECT * FROM subscription_invocations").fetchall()
    assert {row["role"] for row in rows} == set(ROLE_CONTEXT_KEYS)
    assert len(cli.requests) == 6
    assert all(row["actual_model"] == "claude-sonnet-5-5" and row["cost_status"] == "unknown"
               and row["actual_cost_native"] is None for row in rows)
    assert all("guard" not in request.context and "model_route" not in request.context
               and request.provider == "anthropic" for request in cli.requests)
    assert setup.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert setup.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert not setup.transport.calls
    assert setup.office.budget.remaining("deployment") == 5
    assert setup.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE "
        "json_extract(response_json,'$.protected_effect.status')='SUCCEEDED'").fetchone()[0] == 6
    assert protected.controller._has_success("graph-v1")


def test_codex_current_blocker_is_enforced_inside_department_factory(tmp_path):
    setup, _protected, cli = flow(tmp_path, blocked=True)
    identity = setup.add("research")
    assert setup.run("research") == 1
    assert setup.row(identity)["status"] == "FAILED"
    assert not cli.requests and not setup.transport.calls
    assert not setup.assembly.router.readiness(setup.pid)["ready"]


def test_engineer_schema_failure_can_repair_within_commissioned_steps(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    change = _task(setup.clock, setup.pid, setup.baseline, max_steps=3,
                   max_spend=Money(amount="0.5", currency="EUR"))
    setup.engineer.propose(setup.pid, change)
    cli.outputs["leader"] = reply([setup.ref, change.record_id], [
        {"kind": "commission", "change_id": change.record_id}])
    setup.add("leader")
    setup.run("leader")
    cli.outputs["engineer"] = {"wrong": "schema"}
    assert setup.run("engineer") == 1
    assert setup.run("engineer") == 0
    setup.clock.advance(30)
    cli.outputs["engineer"] = {"summary": "Repair the allowlisted context artifact.", "files": [
        {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}
    assert setup.run("engineer") == 1
    row = setup.db.execute("SELECT * FROM tasks WHERE role='engineer'").fetchone()
    assert row["status"] == "SUCCEEDED", row["output_json"]
    assert len([request for request in cli.requests if request.role == "engineer"]) == 2
    assert setup.db.execute("SELECT COUNT(*) FROM subscription_invocations WHERE role='engineer'").fetchone()[0] == 2


def test_uncertain_subscription_outcome_waits_without_replaying_model(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    cli.interrupt = True
    identity = setup.add("research")
    assert setup.run("research") == 1
    assert setup.row(identity)["status"] == "WAITING_EXTERNAL"
    assert setup.run("research") == 0
    assert len(cli.requests) == 1
    assert setup.db.execute("SELECT state FROM subscription_invocations").fetchone()[0] == "UNCERTAIN"


def test_subscription_role_context_cannot_allow_owner_policy():
    with pytest.raises(ValueError, match="allowlist"):
        SubscriptionRuntimeConfig(subscription=SubscriptionConfig(provider="claude_subscription",
            model="claude-sonnet-5-5", allowed_context_keys={"research": ["guard", "owner_policy"]}))


def test_mutable_graph_provider_injection_is_rejected_before_subscription_dispatch(tmp_path):
    source = GRAPH.replace('"guidance":"Confined " + context["role"] + " reasoning."',
                           '"guidance":"test", "provider":"attacker"')
    setup, _protected, cli = flow(tmp_path, source=source)
    identity = setup.add("research")
    assert setup.run("research") == 1
    assert setup.row(identity)["status"] == "FAILED"
    assert not cli.requests
    assert setup.db.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0


def test_subscription_roles_run_with_separately_billed_api_calls_disabled(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    policy = setup.office.execution.authority.active_policy().model_copy(update={"paid_calls_enabled": False,
                                                                               "revision_id": "no-api-policy"})
    setup.office.execution.authority.install_policy(policy, role="owner")
    identity = setup.add("research")
    assert setup.run("research") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    assert len(cli.requests) == 1
    assert setup.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_subscription_recovery_applies_retained_result_without_second_model_call(tmp_path, monkeypatch):
    setup, protected, cli = flow(tmp_path)
    gateway = setup.assembly.gateway
    evaluate = gateway._evaluate
    interrupted = False

    def kill_before_apply(operation, *args, **kwargs):
        nonlocal interrupted
        if operation == "apply_role_result" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("synthetic process interruption after durable result")
        return evaluate(operation, *args, **kwargs)

    monkeypatch.setattr(gateway, "_evaluate", kill_before_apply)
    identity = setup.add("research")
    with pytest.raises(KeyboardInterrupt):
        setup.run("research")
    assert setup.db.execute("SELECT state FROM subscription_invocations").fetchone()[0] == "COMPLETED"
    setup.expire(identity)
    assert setup.run("research") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    assert len(cli.requests) == 1
    assert protected.controller._has_success("graph-v1")


def test_subscription_quota_failure_stops_other_roles_without_api_fallback(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    cli.outputs["research"] = {"bad": "schema"}
    original = cli.execute

    def exhausted(request, **kwargs):
        cli.requests.append(request)
        return CliOutcome('{"type":"result","is_error":true,"error":"rate_limit"}', 1)

    cli.execute = exhausted
    identity = setup.add("research")
    setup.run("research")
    assert setup.row(identity)["status"] == "FAILED"
    cli.execute = original
    identity = setup.add("learning")
    setup.run("learning")
    assert setup.row(identity)["status"] == "FAILED"
    assert len(cli.requests) == 1
    assert not setup.assembly.router.readiness()["ready"]
    assert not setup.transport.calls


def test_running_subscription_process_receives_controller_cancellation(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    passed = []

    def cancelled(request, *, cancel_event=None):
        cli.requests.append(request)
        passed.append(cancel_event)
        assert cancel_event is setup.assembly.gateway.cancelled
        cancel_event.set()
        return CliOutcome("", 137, stopped="cancelled")

    cli.execute = cancelled
    identity = setup.add("research")
    assert setup.run("research") == 1
    assert passed == [setup.assembly.gateway.cancelled]
    assert setup.row(identity)["status"] == "WAITING_EXTERNAL"
    assert len(cli.requests) == 1


def test_engineer_interruption_is_unresolved_and_never_repaired(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    change = _task(setup.clock, setup.pid, setup.baseline, max_steps=3,
                   max_spend=Money(amount="0.5", currency="EUR"))
    setup.engineer.propose(setup.pid, change)
    cli.outputs["leader"] = reply([setup.ref, change.record_id], [
        {"kind": "commission", "change_id": change.record_id}])
    setup.add("leader")
    setup.run("leader")
    cli.interrupt = True
    assert setup.run("engineer") == 1
    assert setup.run("engineer") == 0
    row = setup.db.execute("SELECT * FROM tasks WHERE role='engineer'").fetchone()
    assert row["status"] == "WAITING_EXTERNAL"
    assert len([request for request in cli.requests if request.role == "engineer"]) == 1


def test_subscription_context_contains_only_immutable_role_allowlist(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    setup.add("research")
    setup.run("research")
    assert set(cli.requests[0].context).issubset(ROLE_CONTEXT_KEYS["research"])
    assert "guard" not in cli.requests[0].context and "portfolio" not in cli.requests[0].context


@pytest.mark.parametrize("outcome", ["complete", "failure", "uncertain"])
def test_new_subscription_invocation_id_cannot_repeat_a_task_after_persisted_outcome(tmp_path, monkeypatch, outcome):
    from trade_graph.contracts.models import ModelRequest
    from trade_graph.domain.errors import AuthorityDenied

    setup, _protected, cli = flow(tmp_path)
    if outcome == "failure":
        cli.outputs["research"] = {"wrong": "schema"}
    elif outcome == "uncertain":
        cli.interrupt = True
    adapter = setup.assembly.gateway.gateway.adapter
    invoke = adapter.invoke

    def kill_after_receipt(*args, **kwargs):
        invoke(*args, **kwargs)
        raise KeyboardInterrupt("synthetic interruption after receipt")

    monkeypatch.setattr(adapter, "invoke", kill_after_receipt)
    identity = setup.add("research")
    with pytest.raises(KeyboardInterrupt):
        setup.run("research")
    plan = setup.db.execute("SELECT scope_json FROM protected_rpc_requests WHERE "
                           "json_extract(scope_json,'$.operation')='invoke_model'").fetchone()
    request = ModelRequest.model_validate(json.loads(plan[0])["original_request"])
    with pytest.raises(AuthorityDenied, match="ordinary task invocation identity"):
        setup.assembly.gateway.invoke(request, invocation_id="forbidden-second-attempt", deployment_id="deployment",
            portfolio_id=setup.pid, authorize=lambda: None)
    assert len(cli.requests) == 1
    assert setup.db.execute("SELECT COUNT(*) FROM subscription_invocations WHERE task_id=?",
                            (identity,)).fetchone()[0] == 1


def test_departmental_subscription_profile_requires_all_six_context_roles():
    with pytest.raises(ValueError, match="all six"):
        SubscriptionRuntimeConfig(subscription=SubscriptionConfig(provider="claude_subscription",
            model="claude-sonnet-5-5", allowed_context_keys={"research": ["market"]}))


@pytest.mark.parametrize("role", ["research", "learning", "optimisation"])
@pytest.mark.parametrize("mode", ["paper", "live"])
def test_subscription_journal_envelope_retains_the_persisted_portfolio_mode(tmp_path, role, mode):
    setup, _protected, cli = flow(tmp_path)
    setup.db.execute("UPDATE portfolios SET mode=? WHERE portfolio_id=?", (mode, setup.pid))
    handler = setup.assembly.handlers[role].build({"provider": "anthropic", "model": "claude-sonnet-5-5"})
    task = {"task_id": "mode-task", "root_task_id": "mode-root", "portfolio_id": setup.pid,
            "snapshot_id": "mode-snapshot", "system_version_id": setup.baseline,
            "snapshot": {"evidence_refs": []}}
    assert handler._envelope(task)["mode"] == mode
    assert not cli.requests and not setup.transport.calls


def test_normal_department_uses_configured_subscription_limits(tmp_path):
    setup, _protected, cli = flow(tmp_path)
    configured = setup.assembly.router.config.subscription.model_copy(update={
        "maximum_seconds": 240, "maximum_output_tokens": 8192})
    setup.assembly.router.config.subscription = configured
    setup.assembly.router.adapter.config = configured
    setup.db.execute("DELETE FROM role_allocations")
    setup.db.execute("DELETE FROM deployment_budget")
    setup.office.scheduler.add_task(role="research", objective="Normal public research",
        portfolio_id=setup.pid, allocated_spend=__import__("decimal").Decimal("0"))
    assert setup.run("research") == 1
    assert cli.requests[0].timeout_seconds == 240
    assert cli.requests[0].max_output_tokens == 8192


def test_zero_money_commission_uses_subscription_without_fake_api_budget(tmp_path):
    from decimal import Decimal

    setup, _protected, cli = flow(tmp_path)
    setup.db.execute("DELETE FROM role_allocations")
    setup.db.execute("DELETE FROM deployment_budget")
    change = _task(setup.clock, setup.pid, setup.baseline, max_steps=2,
                   max_spend=Money(amount="0", currency="EUR"))
    setup.engineer.propose(setup.pid, change)
    cli.outputs["leader"] = reply([setup.ref, change.record_id], [
        {"kind": "commission", "change_id": change.record_id}])
    leader = setup.office.scheduler.add_task(role="leader", objective="Commission within subscription allowance",
        portfolio_id=setup.pid, allocated_spend=Decimal("0"))
    setup.run("leader")
    assert setup.row(leader)["status"] == "SUCCEEDED", setup.row(leader)["output_json"]
    assert setup.run("engineer") == 1
    engineer = setup.db.execute("SELECT * FROM tasks WHERE role='engineer'").fetchone()
    assert engineer["status"] == "SUCCEEDED", engineer["output_json"]
    assert setup.db.execute("SELECT COUNT(*) FROM deployment_budget").fetchone()[0] == 0
    receipt = setup.db.execute("SELECT * FROM subscription_invocations WHERE role='engineer'").fetchone()
    assert receipt["cost_status"] == "unknown" and receipt["actual_cost_native"] is None


def test_router_keeps_verified_fallback_available_when_primary_quota_pauses(tmp_path):
    from trade_graph.adapters.models.subscription import SubscriptionJournal

    setup, _protected, cli = flow(tmp_path)
    adapter = setup.assembly.router.adapter
    config = adapter.config.model_copy(update={"provider": "codex_subscription", "model": "gpt-6.1-sol"})
    readiness = SubscriptionReadiness("codex_subscription", "0.160.1", True, (), {}, "chatgpt", "linux-bubblewrap")
    adapter.fallbacks = (SubscriptionAdapter(config, readiness, cli),)
    journal = SubscriptionJournal(setup.db, setup.clock)
    journal.pause_ai(adapter.config.provider, {})
    status = setup.assembly.router.readiness(setup.pid)
    assert status["ready"] and status["available_subscription_routes"] == ["codex_subscription"]
    assert status["application_automatic_fallback"]
