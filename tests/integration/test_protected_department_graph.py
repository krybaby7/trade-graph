"""Six real durable workflows behind actual-host confined mutable reasoning."""

import asyncio
import hashlib
import json
import sqlite3
import threading

import pytest
from tests.integration.test_engineer import POLICY, _task
from tests.integration.test_runtime_models import RuntimeFlow
from tests.leadership_support import reply

from trade_graph.application.protected_departments import assemble_protected_handlers
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.application.runtime_models import MODEL_ROLES
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.domain.money import Money
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, document_sha256, protected_package_sha256

GRAPH = '''
def graph(context):
    assert "guard" not in context["snapshot"] and "model_route" not in context["snapshot"]
    if context["operation"] == "invoke_model":
        return {"node":"invoke_model", "guidance":"Confined " + context["role"] + " reasoning."}
    payload = context["result"]
    key = "rationale" if context["role"] in ("trader", "leader") else "summary"
    payload[key] = "Confined completion: " + payload[key]
    return {"node":"apply_role_result", "payload":payload}
'''


def protected_flow(tmp_path, *, source=GRAPH, approved_sources=()):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="deployment", approved_source_sha256=tuple(
            hashlib.sha256(item.encode()).hexdigest() for item in (source, *approved_sources)),
        operations=("submit_decision", "invoke_model", "apply_role_result"))
    runtime = ProtectedPaperRuntime(database=flow.db, clock=flow.clock, execution=flow.office.execution,
        manifest=manifest, capability_key=b"synthetic-parent-department-key-41", instance_id="departments")
    runtime.controller.admit_release(release_id="graph-v1", source_text=source)
    runtime.controller.activate_release("graph-v1")
    flow.assembly = assemble_protected_handlers(flow.office, flow.secretary, flow.engineer, flow.runtime, flow.config,
        protected_runtime=runtime, workspace_root=flow.source.parent / "private-engineering", api_keys={})
    outputs = flow.assembly.gateway.scripted.outputs
    common = {"evidence_refs": [flow.ref], "summary": "Retained bounded evidence.", "outcome": "Insufficient sample."}
    for role, extra in (("research", {"findings": []}), ("learning", {"lessons": []}),
                        ("optimisation", {"proposals": []})):
        outputs[role] = {**common, **extra}
    outputs["trader"] = {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
        "invalidation": "New evidence.", "experiment": False, "evidence_ids": [], "no_action_reason": "No signal."}
    outputs["leader"] = reply([flow.ref])
    outputs["engineer"] = {"summary": "Reduce retained general context.", "files": [
        {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}
    original_complete = flow.assembly.gateway.scripted.complete

    def complete(request):
        payload = outputs[request.role]
        if "evidence_refs" in payload:
            payload = {**payload, "evidence_refs": request.context["evidence_refs"][:20]}
        scoped = request.model_copy(update={"context": {**request.context, "scripted_result": payload}})
        return original_complete(scoped)

    flow.assembly.gateway.scripted.complete = complete
    return flow, runtime


def test_all_six_real_workflows_have_confined_graph_steps_and_protected_receipts(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    for role in ("research", "trader", "learning", "optimisation"):
        identity = flow.add(role)
        assert flow.run(role) == 1
        assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    proposal = _task(flow.clock, flow.pid, flow.baseline, max_steps=1,
                     max_spend=Money(amount="0.5", currency="EUR"))
    flow.engineer.propose(flow.pid, proposal)
    flow.assembly.gateway.scripted.outputs["leader"] = reply([flow.ref, proposal.record_id], [
        {"kind": "commission", "change_id": proposal.record_id}])
    leader = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(leader)["status"] == "SUCCEEDED", flow.row(leader)["output_json"]
    assert flow.run("engineer") == 1
    engineer = flow.db.execute("SELECT * FROM tasks WHERE role='engineer'").fetchone()
    assert engineer["status"] == "SUCCEEDED", engineer["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state='READY'").fetchone()[0] == 1
    receipts = flow.db.execute("SELECT r.synthetic,b.role FROM usage_receipts r JOIN budget_reservations b "
                              "USING(reservation_id)").fetchall()
    assert {row["role"] for row in receipts} == set(MODEL_ROLES)
    assert all(row["synthetic"] == 1 for row in receipts)
    assert not flow.transport.calls
    assert flow.office.budget.remaining("deployment") == 5
    assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE state='APPLIED'").fetchone()[0] == 12
    assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE "
                           "json_extract(response_json,'$.protected_effect.status')='SUCCEEDED'").fetchone()[0] == 6
    assert runtime.controller._has_success("graph-v1")
    result = flow.db.execute("SELECT document_json FROM role_results WHERE role='research'").fetchone()[0]
    assert "Confined completion" in result


def test_opt_in_runtime_runs_department_with_independent_management(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    result = asyncio.run(runtime.run_departments(flow.worker, flow.assembly, flow.pid))
    assert result["tasks"] == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]


def test_department_batch_leaves_competing_portfolio_task_unclaimed(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    foreign = flow.office.scheduler.add_task(role="research", objective="Other portfolio work.",
        portfolio_id="other-protected-portfolio", max_attempts=1)
    flow.db.execute("UPDATE tasks SET priority=100 WHERE task_id=?", (foreign,))
    identity = flow.add("research")
    result = asyncio.run(runtime.run_departments(flow.worker, flow.assembly, flow.pid, maximum_tasks=2))
    assert result["tasks"] == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    assert flow.row(foreign)["status"] == "QUEUED"
    assert flow.row(foreign)["lease_token"] is None
    assert flow.row(foreign)["attempts_used"] == 0


@pytest.mark.parametrize("field", ["provider", "price_card_id", "max_output_tokens", "tools", "role", "budget",
                                  "passed"])
def test_confined_graph_cannot_supply_protected_model_controls(tmp_path, field):
    source = GRAPH.replace('"guidance":"Confined " + context["role"] + " reasoning."',
                           '"guidance":"test", ' + repr(field) + ':"attacker"')
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_original_role_schema_rejects_mutable_self_attestation_after_receipt(tmp_path):
    source = GRAPH.replace('payload[key] =', 'payload["passed"] = True\n    payload[key] =')
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM role_results WHERE status='SUCCEEDED'").fetchone()[0] == 0


def test_typed_but_semantically_invalid_result_cannot_become_rollback_baseline(tmp_path):
    source = GRAPH.replace('payload[key] =', '''if context["role"] == "research":
        payload["findings"] = [{"source_ref":"outside-snapshot", "question":"Observe?", "claim":"Unproved.",
            "counterevidence":"Absent source.", "invalidation":"New source.", "expires_after_seconds":60}]
    payload[key] =''')
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    completed = flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE "
        "json_extract(scope_json,'$.operation')='apply_role_result' AND state='APPLIED'").fetchone()[0]
    assert completed == 1
    assert not runtime.controller._has_success("graph-v1")
    assert runtime.controller.status()["status"] == "RUNNING", "semantic parent refusal does not blame graph framing"


def test_effect_acknowledgment_failure_rolls_back_role_effects_preserving_cost(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    flow.add("research")
    flow.db.execute("""CREATE TRIGGER fail_effect_ack BEFORE UPDATE ON protected_rpc_requests
        WHEN json_extract(NEW.response_json,'$.protected_effect.status')='SUCCEEDED'
        BEGIN SELECT RAISE(ABORT,'synthetic protected acknowledgment storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic protected acknowledgment storage failure"):
        flow.run("research")
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM role_results").fetchone()[0] == 0
    assert not runtime.controller._has_success("graph-v1")
    assert runtime.controller.status()["status"] == "RUNNING", "storage failure does not blame the mutable process"


def test_failed_department_source_rolls_back_real_effect_predecessor_preserving_cost_tasks_and_management(
        tmp_path, monkeypatch):
    broken = GRAPH.replace('payload[key] =',
                           'raise RuntimeError("synthetic confined result failure")\n    payload[key] =')
    flow, runtime = protected_flow(tmp_path, approved_sources=(broken,))
    predecessor = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(predecessor)["status"] == "SUCCEEDED"
    runtime.controller.admit_release(release_id="graph-broken", source_text=broken)
    runtime.controller.activate_release("graph-broken")
    identity = flow.add("research")
    ticks = []
    original = runtime.financial.reconcile

    async def manage():
        ticks.append("protected management")
        await original()

    monkeypatch.setattr(runtime.financial, "reconcile", manage)
    result = asyncio.run(runtime.run_departments(flow.worker, flow.assembly, flow.pid))
    assert result["tasks"] == 1 and len(ticks) >= 2
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.row(predecessor)["status"] == "SUCCEEDED"
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 2
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 2
    assert runtime.controller.status()["active_release_id"] == "graph-v1"
    assert runtime.controller.status()["status"] == "RUNNING"
    assert not runtime.controller._has_success("graph-broken")


@pytest.mark.parametrize("change", ["release", "policy", "cancel"])
def test_delayed_department_failure_preserves_newer_release_owner_authority_and_cancellation(
        tmp_path, monkeypatch, change):
    import trade_graph.kernel.department_gateway as boundary

    broken = 'def graph(context):\n    raise RuntimeError("synthetic mutable failure")\n'
    flow, runtime = protected_flow(tmp_path, source=broken, approved_sources=(GRAPH,))
    runtime.controller.admit_release(release_id="graph-new", source_text=GRAPH)
    identity = flow.add("research")
    original = boundary.run_bounded

    def failed_after_authority_change(*args, **kwargs):
        response = original(*args, **kwargs)
        if change == "release":
            runtime.controller.activate_release("graph-new")
        elif change == "policy":
            policy = runtime.financial.authority.active_policy().model_copy(update={"revision_id":"new-owner-policy"})
            runtime.financial.authority.install_policy(policy, role="owner")
        else:
            flow.assembly.gateway.cancelled.set()
        return response

    monkeypatch.setattr(boundary, "run_bounded", failed_after_authority_change)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    state = runtime.controller.status()
    assert state["status"] == "RUNNING"
    assert state["active_release_id"] == ("graph-new" if change == "release" else "graph-v1")
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0


def _quote(flow):
    flow.office.execution.register_instrument(InstrumentRules(venue="paper", symbol="BTC/USD", base_asset="BTC",
        quote_asset="USD", price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
        min_notional="1", synthetic=True))
    flow.office.execution.save_observation(Observation(observation_id="protected-graph-quote", venue="paper",
        symbol="BTC/USD", event_time_utc=flow.clock.now(), available_at_utc=flow.clock.now(),
        bid="99", ask="100", volume="1", kind="quote", source="synthetic-fixture"))


@pytest.mark.parametrize("operation", ["invoke_model", "apply_role_result"])
def test_financial_endpoint_refuses_department_operations_even_with_financial_capability(tmp_path, operation):
    flow, runtime = protected_flow(tmp_path)
    _quote(flow)
    context = runtime.financial.issue("departments", flow.pid, "BTC/USD")
    with pytest.raises(AuthorityDenied):
        runtime.financial.dispatch(json.dumps({"request_id":context["request_id"],
            "capability":context["capability"], "operation":operation,
            "decision":{"action":"hold", "rationale":"Unrelated operation."}}))
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert not runtime.controller._has_success("graph-v1")


def test_graph_contract_identity_and_graph_only_financial_cycle_fail_closed(tmp_path):
    flow, runtime = protected_flow(tmp_path)
    release = runtime.controller.admit_release(release_id="graph-v1", source_text=GRAPH)
    assert release["build_digest"] == document_sha256({"source_sha256":hashlib.sha256(GRAPH.encode()).hexdigest(),
        "contract":"decision-and-departmental-graph-v1", "operations":runtime.financial.manifest.operations})
    _quote(flow)
    result = asyncio.run(runtime.cycle(flow.pid, "BTC/USD"))
    assert result["status"] == "MUTABLE_REJECTED"
    assert result["recovery"] == "MANAGE_ONLY"
    assert not runtime.controller._has_success("graph-v1")


def test_known_cost_and_confined_result_recover_without_provider_or_child_replay(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    original = flow.assembly.gateway.recover_reply

    def interrupt_before_apply(*args):
        raise KeyboardInterrupt("synthetic crash after receipt/graph result but before protected effects")

    monkeypatch.setattr(flow.assembly.gateway, "recover_reply", interrupt_before_apply)
    with pytest.raises(KeyboardInterrupt):
        flow.run("research")
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM role_results").fetchone()[0] == 0
    assert not runtime.controller._has_success("graph-v1"), "a validated result has not applied protected effects"
    before = flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests").fetchone()[0]
    monkeypatch.setattr(flow.assembly.gateway, "recover_reply", original)
    flow.expire(identity)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests").fetchone()[0] == before
    assert "Confined completion" in flow.row(identity)["output_json"]
    assert runtime.controller._has_success("graph-v1")


def test_interrupted_provider_attempt_recovers_uncertain_hold_without_redispatch(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    calls = []

    def interrupted(request):
        calls.append("dispatched")
        raise KeyboardInterrupt("synthetic response lost after possible dispatch")

    monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", interrupted)
    with pytest.raises(KeyboardInterrupt):
        flow.run("research")
    flow.expire(identity)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "WAITING_EXTERNAL", flow.row(identity)["output_json"]
    assert calls == ["dispatched"]
    assert flow.invocation(identity)["state"] == "UNCERTAIN"
    assert flow.db.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert flow.office.budget.remaining("deployment") == 5  # Synthetic uncertainty never becomes actual spending.


def test_unknown_usage_preserves_hold_and_blocks_completion_on_restart(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    original = flow.assembly.gateway.scripted.complete
    calls = []

    def unknown_usage(request):
        calls.append("response")
        return original(request).model_copy(update={"usage": None})

    monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", unknown_usage)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "WAITING_EXTERNAL"
    assert flow.invocation(identity)["state"] == "UNCERTAIN"
    flow.db.execute("UPDATE tasks SET status='QUEUED' WHERE task_id=?", (identity,))
    assert flow.run("research") == 1
    assert calls == ["response"]
    assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE "
                           "json_extract(scope_json,'$.operation')='apply_role_result'").fetchone()[0] == 0


def test_stale_release_after_model_response_retains_receipt_and_prevents_effect(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    original = flow.assembly.gateway.scripted.complete

    def response_then_revoke(request):
        result = original(request)
        status = runtime.controller.status()
        runtime.controller._recover_mutable(status["active_release_id"], status["generation"])
        return result

    monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", response_then_revoke)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert runtime.controller.status()["status"] == "MANAGE_ONLY"


def test_expired_plan_cannot_start_new_provider_effect(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    original = flow.assembly.gateway._evaluate

    def plan_then_expire(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "invoke_model":
            flow.clock.advance(16)
        return result

    monkeypatch.setattr(flow.assembly.gateway, "_evaluate", plan_then_expire)
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert not runtime.controller._has_success("graph-v1"), "a graph plan is not a successful protected effect"
    runtime.controller.admit_release(release_id="graph-v2", source_text=GRAPH)
    runtime.controller.activate_release("graph-v2")
    state = runtime.controller.status()
    assert state["previous_release_id"] is None
    assert runtime.controller._recover_mutable("graph-v2", state["generation"]) == "MANAGE_ONLY"


def test_real_os_department_process_cannot_open_database_keys_or_socket(tmp_path, monkeypatch):
    attack = '''    assert "PROTECTED_DEPARTMENT_KEY" not in os.environ
    for action in (lambda: os.open(DBPATH, os.O_RDONLY), lambda: socket.socket()):
        try:
            action()
            raise AssertionError("confinement bypass")
        except PermissionError:
            pass
    assert "guard"'''
    source = ('import os, socket\n' + GRAPH.replace('    assert "guard"', attack)).replace(
        "DBPATH", repr(str(tmp_path / "engineer.sqlite")))
    monkeypatch.setenv("PROTECTED_DEPARTMENT_KEY", "synthetic-parent-secret")
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    assert "synthetic-parent-secret" not in flow.row(identity)["output_json"]


@pytest.mark.parametrize("attack", [
    'os.write(1, b"malformed-prefix")',
    'os.write(1, b\'{"duplicate":1,"duplicate":2}\'); os._exit(0)',
    'os.write(1, b"[" * 2000 + b"]" * 2000); os._exit(0)',
    'os.write(1, b"\\xff")',
])
def test_actual_child_malformed_duplicate_deep_unicode_output_fails_task_and_preserves_management(tmp_path, attack):
    source = 'import os\n' + GRAPH.replace('    assert "guard"', '    ' + attack + '\n    assert "guard"')
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("research")
    flow.db.execute("UPDATE tasks SET priority=100 WHERE task_id=?", (identity,))
    pending = flow.add("learning")
    result = asyncio.run(runtime.run_departments(flow.worker, flow.assembly, flow.pid, maximum_tasks=2))
    assert result["tasks"] == 1
    assert flow.row(identity)["status"] == "FAILED", flow.row(identity)["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE state='REVOKED'").fetchone()[0] == 1
    assert not runtime.controller._has_success("graph-v1")
    assert runtime.controller.status()["status"] == "MANAGE_ONLY"
    assert flow.row(pending)["status"] == "QUEUED"
    assert flow.row(pending)["attempts_used"] == 0


def test_slow_model_keeps_management_running_and_cancellation_drains_receipt(tmp_path, monkeypatch):
    flow, runtime = protected_flow(tmp_path)
    identity = flow.add("research")
    entered, release = threading.Event(), threading.Event()
    original = flow.assembly.gateway.scripted.complete
    management = []
    reconcile = runtime.financial.reconcile

    def slow(request):
        entered.set()
        assert release.wait(timeout=10)
        return original(request)

    async def managed():
        management.append("tick")
        await reconcile()

    monkeypatch.setattr(flow.assembly.gateway.scripted, "complete", slow)
    monkeypatch.setattr(runtime.financial, "reconcile", managed)

    async def scenario():
        running = asyncio.create_task(runtime.run_departments(flow.worker, flow.assembly, flow.pid))
        for _ in range(300):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        before = len(management)
        for _ in range(300):
            if len(management) >= before + 2:
                break
            await asyncio.sleep(0.01)
        assert len(management) >= before + 2
        running.cancel()
        await asyncio.sleep(0.05)
        assert not running.done(), "controller must drain the outstanding protected provider attempt"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert flow.row(identity)["status"] == "FAILED"


@pytest.mark.parametrize("quantity", ["1e999999999", "0e-999999999", "NaN", "1.0000000000000000000000000001"])
def test_decimal_expansion_precision_attacks_refuse_before_parent_financial_effect(tmp_path, quantity):
    source = GRAPH.replace('payload[key] =', '''if context["role"] == "trader":
        payload["action"] = "enter"
        payload["symbol"] = "BTC/USD"
        payload["quantity"] = QUANTITY
    payload[key] =''').replace("QUANTITY", repr(quantity))
    flow, runtime = protected_flow(tmp_path, source=source)
    identity = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
