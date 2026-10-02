"""Runtime factory/worker/provider paths; no credentials or external calls."""

import asyncio
import json
from decimal import Decimal

import pytest
from tests.integration.test_artifact_consumers import _choice
from tests.integration.test_engineer import POLICY, _stack, _task
from tests.leadership_support import reply, stack

from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.authority import paper_owner_policy
from trade_graph.application.runtime_models import MODEL_ROLES, RuntimeModelConfig, assemble_handlers
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import Observation, PriceCard
from trade_graph.domain.money import Money


class RecordingTransport:
    def __init__(self):
        self.calls = []
        self.outputs = {}
        self.omit_usage = False
        self.interrupt = False
        self.resolved_model = None

    def post_json(self, url, body, headers, *, timeout_seconds=None):
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        if self.interrupt:
            raise KeyboardInterrupt("process interrupted after possible provider dispatch")
        if "input" in body:
            context = json.loads(body["input"][1]["content"])
            schema = body["text"]["format"]["name"]
        else:
            context = json.loads(body["messages"][0]["content"])
            schema = next(key for key in self.outputs if key == "TraderReply")
        output = self.outputs.get(schema)
        payload = output(context) if callable(output) else output
        response = {"id": f"fixture-{len(self.calls)}", "model": self.resolved_model or body["model"]}
        if "input" in body:
            response["output"] = [{"type": "message", "content": [{"type": "output_text",
                                                                    "text": json.dumps(payload)}]}]
        else:
            response["content"] = [{"type": "text", "text": json.dumps(payload)}]
        if not self.omit_usage:
            response["usage"] = {"input_tokens": 10, "output_tokens": 5}
        return response


def _card(card_id, *, provider="openai", model="gpt-6-luna"):
    return PriceCard(
        price_card_id=card_id, provider=provider, model=model,
        endpoint={"openai": "https://api.openai.com/v1/responses",
                  "anthropic": "https://api.anthropic.com/v1/messages", "scripted": "scripted"}[provider],
        currency="EUR", input_per_million="0.1", output_per_million="0.1",
        effective_at="2026-01-01", verified_at="2026-01-01", source_id="synthetic-price-fixture",
        tier="standard", context_band="short",
    )


class RuntimeFlow:
    def __init__(self, tmp_path, *, provider="openai", routing=None, paid=True, owner_paid=True,
                 keys=None, overrides=None):
        self.clock, self.db, self.pid, self.source, self.engineer, self.versions = _stack(tmp_path)
        if routing is not None:
            (self.source / "artifacts" / "model_routing.json").write_text(json.dumps(routing))
        self.baseline = self.engineer.baseline(tmp_path / "baseline")
        self.versions.ensure(self.pid, "v1", self.baseline)
        self.office, self.secretary, _gateway, _leader = stack(self.db, self.clock, self.engineer.ledger, self.pid)
        self.office.execution.authority.install_policy(paper_owner_policy(revision_id="runtime-policy").model_copy(
            update={"paid_calls_enabled": owner_paid, "allowed_change_classes": [
                "artifact_config", "approved_model_routing"]}), role="owner")
        model = "scripted" if provider == "scripted" else "gpt-6-luna"
        self.office.budget.seed_card(_card("primary", provider=provider, model=model))
        self.office.budget.seed_card(_card("stronger", model="gpt-6.1-sol"))
        config = {"paid_calls_enabled": paid, "approved_price_card_ids": ["primary", "stronger"],
                  "role_routes": {role: "primary" for role in MODEL_ROLES}}
        config.update(overrides or {})
        self.config = RuntimeModelConfig.model_validate(config)
        self.transport = RecordingTransport()
        self.keys = {"openai": "synthetic-private-credential"} if keys is None else keys
        self.runtime = ArtifactRuntime(self.versions, self.office.scheduler)
        self.assembly = self.bind()
        self.ref = self.secretary.report(self.pid, role="research", kind="initial",
            summary="Synthetic initial evidence, no profitability claim.",
            evidence_refs=[self.baseline], source_key="initial")
        self.transport.outputs.update({
            "TraderReply": _choice(),
            "LeaderReply": lambda context: reply(context["evidence_refs"]),
            "ResearchReply": lambda context: {"evidence_refs": context["evidence_refs"][:20],
                "summary": "Research inspected the pinned evidence.", "outcome": "Insufficient evidence.",
                "findings": []},
            "LearningReply": lambda context: {"evidence_refs": context["evidence_refs"][:20],
                "summary": "Learning retained process and outcome separately.", "outcome": "Insufficient sample.",
                "lessons": []},
            "OptimisationReply": lambda context: {"evidence_refs": context["evidence_refs"][:20],
                "summary": "Optimisation inspected actual task and expense evidence.", "outcome": "Bounded proposal.",
                "proposals": []},
        })

    def bind(self):
        assembly = assemble_handlers(self.office, self.secretary, self.engineer, self.runtime, self.config,
            workspace_root=self.source.parent / "private-engineering", api_keys=self.keys, transport=self.transport)
        self.worker = RoleWorker(self.office.scheduler, owner="runtime-worker", system_version_id=self.baseline,
            reconcile=lambda: asyncio.run(self.office.execution.reconcile()), artifact_runtime=self.runtime)
        return assembly

    def add(self, role, **updates):
        return self.office.scheduler.add_task(role=role, objective="Review the bounded current evidence.",
            portfolio_id=self.pid, max_attempts=1, allocated_spend=Decimal("1"), **updates)

    def run(self, role=None):
        self.secretary.process(self.pid, route=False)
        handlers = self.assembly.handlers if role is None else {role: self.assembly.handlers[role]}
        return self.worker.run_available(handlers)

    def row(self, task_id):
        return self.db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()

    def invocation(self, task_id):
        return self.db.execute("SELECT * FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()

    def expire(self, task_id):
        self.clock.advance(200)
        self.db.execute("UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
                        (self.office.scheduler.now(), task_id))


def test_factory_runs_all_six_real_handlers_and_typed_journals(tmp_path):
    flow = RuntimeFlow(tmp_path)
    assert set(flow.assembly.handlers) == set(MODEL_ROLES)
    observation = Observation(observation_id="source-observation", venue="paper", symbol="BTC/USD",
        event_time_utc=flow.clock.now(), available_at_utc=flow.clock.now(),
        bid="99", ask="100", volume="1", kind="quote", source="synthetic-fixture")
    flow.office.execution.save_observation(observation)
    flow.transport.outputs["ResearchReply"] = lambda context: {
        "evidence_refs": context["evidence_refs"][:20], "summary": "Inspect visible liquidity.", "outcome": "Unproven.",
        "findings": [{"source_ref": "source-observation", "question": "Visible spread?", "claim": "One-unit spread.",
            "counterevidence": "One observation does not establish a trading edge.",
            "invalidation": "New observation supersedes this snapshot.", "expires_after_seconds": 3600}],
    }
    research = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(research)["status"] == "SUCCEEDED", flow.row(research)["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    trader = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(trader)["status"] == "SUCCEEDED", flow.row(trader)["output_json"]
    decision = json.loads(flow.row(trader)["output_json"])["decision_id"]
    flow.transport.outputs["LearningReply"] = lambda context: {
        "evidence_refs": context["evidence_refs"][:20], "summary": "Retain the hold decision.", "outcome": "Tentative.",
        "lessons": [{"lesson_id": None, "observation": "Held from the available snapshot.",
            "supporting_cases": [decision], "counterexamples": ["Later price may rise."],
            "explanation": "Hold is a valid discretionary option.", "proposed_improvement": "Inspect future outcomes.",
            "validation_method": "Forward sample.", "scope": "BTC/USD paper", "sample_note": "One observation.",
            "linked_decisions": [decision], "confidence_category": "tentative", "status": "tentative",
            "process_assessment": "valid_thesis", "outcome_sign": "unknown"}],
    }
    learning = flow.add("learning")
    assert flow.run("learning") == 1
    assert flow.row(learning)["status"] == "SUCCEEDED", flow.row(learning)["output_json"]
    lesson = json.loads(flow.db.execute("SELECT document_json FROM lessons").fetchone()[0])
    assert lesson["linked_decisions"] == [decision] and lesson["counterexamples"]
    flow.transport.outputs["OptimisationReply"] = lambda context: {
        "evidence_refs": context["evidence_refs"][:20], "summary": "A measurable review.", "outcome": "Proposal only.",
        "proposals": [{"issue": "Sparse evidence.", "resources": "Existing funded envelope.",
            "interval": "Next review.", "modification": "Retain only relevant lessons.",
            "expected_benefit": "Bounded context.", "quality_risk": "Missing rare evidence.",
            "validation_metrics": ["required obligations retained"]}],
    }
    optimisation = flow.add("optimisation")
    assert flow.run("optimisation") == 1
    assert flow.row(optimisation)["status"] == "SUCCEEDED", flow.row(optimisation)["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
    proposal = _task(flow.clock, flow.pid, flow.baseline, max_steps=1, max_spend=Money(amount="0.5", currency="EUR"))
    flow.engineer.propose(flow.pid, proposal)
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "commission", "change_id": proposal.record_id}])
    leader = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(leader)["status"] == "SUCCEEDED", flow.row(leader)["output_json"]
    flow.transport.outputs["EngineerPatch"] = {"summary": "Reduce the general lesson cap.", "files": [
        {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}
    assert flow.run("engineer") == 1
    engineering = flow.db.execute("SELECT * FROM tasks WHERE role = 'engineer'").fetchone()
    assert engineering["status"] == "SUCCEEDED", engineering["output_json"]
    candidate = flow.db.execute("SELECT * FROM candidates").fetchone()
    assert candidate["state"] == "READY"
    assert flow.db.execute("SELECT COUNT(*) FROM candidate_attestations WHERE exit_code = 0").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 6
    receipts = flow.db.execute("SELECT r.*,b.role FROM usage_receipts r JOIN budget_reservations b "
                              "USING(reservation_id)").fetchall()
    assert {row["role"] for row in receipts} == set(MODEL_ROLES)
    assert all(row["synthetic"] == 0 for row in receipts)
    assert flow.office.budget.remaining("deployment") < Decimal("5")
    for call in flow.transport.calls:
        assert call["body"]["model"] == "gpt-6-luna"
        assert "synthetic-private-credential" not in json.dumps(call["body"])


@pytest.mark.parametrize("options,reason", [
    ({"paid": False}, "runtime paid calls are disabled"),
    ({"keys": {}}, "private provider credential"),
    ({"owner_paid": False}, "owner has not enabled paid calls"),
    ({"overrides": {"role_routes": {}}}, "no configured model route"),
    ({"routing": {"schema_version": 1, "routes": {"trader": "unapproved"}}}, "protected approved"),
    ({"overrides": {"approved_price_card_ids": ["absent"], "role_routes": {"trader": "absent"}}},
     "no persisted price card"),
])
def test_runtime_refuses_locally_before_reservation_or_provider(options, reason, tmp_path):
    flow = RuntimeFlow(tmp_path, **options)
    task_id = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(task_id)["status"] == "FAILED"
    assert reason in flow.row(task_id)["output_json"]
    assert not flow.transport.calls
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("role", ["research", "learning", "optimisation", "leader", "trader"])
def test_unknown_runtime_billing_waits_and_never_replays(role, tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.transport.omit_usage = True
    task_id = flow.add(role)
    assert flow.run(role) == 1
    assert flow.row(task_id)["status"] == "WAITING_EXTERNAL"
    assert flow.invocation(task_id)["state"] == "UNCERTAIN"
    assert flow.db.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert flow.office.budget.remaining("deployment") < Decimal("5")
    flow.db.execute("UPDATE tasks SET status = 'QUEUED' WHERE task_id = ?", (task_id,))
    flow.assembly = flow.bind()
    assert flow.run(role) == 1
    assert len(flow.transport.calls) == 1
    assert flow.row(task_id)["status"] == "WAITING_EXTERNAL"


@pytest.mark.parametrize("role", ["research", "learning", "optimisation", "leader", "trader"])
def test_runtime_crash_recovery_retains_cost_and_no_second_dispatch(role, tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.transport.interrupt = True
    task_id = flow.add(role)
    with pytest.raises(KeyboardInterrupt):
        flow.run(role)
    assert flow.invocation(task_id)["state"] == "DISPATCHED"
    flow.expire(task_id)
    flow.assembly = flow.bind()
    assert flow.run(role) == 1
    assert len(flow.transport.calls) == 1
    assert flow.row(task_id)["status"] == "WAITING_EXTERNAL"
    assert flow.invocation(task_id)["state"] == "UNCERTAIN"


def test_artifact_selects_actual_model_and_pins_provenance(tmp_path):
    flow = RuntimeFlow(tmp_path, routing={"schema_version": 1, "routes": {"trader": "stronger"}})
    task_id = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED"
    assert flow.transport.calls[0]["body"]["model"] == "gpt-6.1-sol"
    request = json.loads(flow.invocation(task_id)["request_json"])
    assert request["provider"] == "openai" and request["model"] == "gpt-6.1-sol"
    assert request["context"]["model_route"]["price_card_id"] == "stronger"
    assert request["context"]["model_route"]["source"] == "artifacts/model_routing.json"
    assert request["context"]["model_route"]["artifact"]["artifact_hash"] == flow.baseline
    assert request["system_version_id"] == flow.baseline
    receipt = flow.db.execute("SELECT r.model,b.price_card_id,b.system_version_id FROM usage_receipts r "
                             "JOIN budget_reservations b USING(reservation_id)").fetchone()
    assert tuple(receipt) == ("gpt-6.1-sol", "stronger", flow.baseline)
    assert flow.assembly.router.readiness(flow.pid)["ready"]


def test_one_provider_configuration_needs_no_second_key(tmp_path):
    flow = RuntimeFlow(tmp_path, keys={"openai": "synthetic-private-credential"})
    for role in ["research", "trader", "learning", "optimisation", "leader"]:
        task_id = flow.add(role)
        assert flow.run(role) == 1
        assert flow.row(task_id)["status"] == "SUCCEEDED", flow.row(task_id)["output_json"]
    assert len(flow.transport.calls) == 5
    assert all(call["url"] == "https://api.openai.com/v1/responses" for call in flow.transport.calls)


def test_optimisation_commissions_tested_routing_activation_restart_and_rollback(tmp_path):
    flow = RuntimeFlow(tmp_path)
    before = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.transport.calls[-1]["body"]["model"] == "gpt-6-luna"
    flow.transport.outputs["OptimisationReply"] = lambda context: {
        "evidence_refs": context["evidence_refs"][:20], "summary": "Evaluate a stronger approved model.",
        "outcome": "Commission a bounded routing experiment.", "proposals": [],
        "changes": [{"objective": "Select the approved stronger Trader model.",
            "allowed_classes": ["approved_model_routing"], "allowed_paths": ["artifacts/model_routing.json"],
            "invariants": ["paid model approval remains protected"], "max_spend_eur": "0.5", "max_steps": 1,
            "test_plan": "Independent artifact checker and protected card registry.",
            "success_criteria": "New tasks invoke the selected model with receipts.",
            "rollback_criteria": "Restore prior artifact pointer.", "expires_after_seconds": 3600}],
    }
    optimisation = flow.add("optimisation")
    assert flow.run("optimisation") == 1
    assert flow.row(optimisation)["status"] == "SUCCEEDED", flow.row(optimisation)["output_json"]
    change_id = json.loads(flow.row(optimisation)["output_json"])["change_ids"][0]
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "commission", "change_id": change_id}])
    leader = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(leader)["status"] == "SUCCEEDED", flow.row(leader)["output_json"]
    flow.transport.outputs["EngineerPatch"] = {"summary": "Use the approved immutable stronger price card.",
        "files": [{"path": "artifacts/model_routing.json",
                   "content": json.dumps({"schema_version": 1, "routes": {"trader": "stronger"}})}]}
    assert flow.run("engineer") == 1
    engineering = flow.db.execute("SELECT * FROM tasks WHERE role = 'engineer'").fetchone()
    assert engineering["status"] == "SUCCEEDED", engineering["output_json"]
    candidate_id = json.loads(engineering["output_json"])["candidate_id"]
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "activate", "candidate_id": candidate_id}])
    activate = flow.add("leader")
    assert flow.run("leader") == 1
    assert flow.row(activate)["status"] == "SUCCEEDED", flow.row(activate)["output_json"]
    activated_hash = flow.versions.current_hash(flow.pid)
    assert activated_hash != flow.baseline
    after = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(after)["status"] == "SUCCEEDED", flow.row(after)["output_json"]
    assert flow.transport.calls[-1]["body"]["model"] == "gpt-6.1-sol"
    assert flow.invocation(after)["system_version_id"] == activated_hash
    flow.assembly = flow.bind()
    restarted = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.transport.calls[-1]["body"]["model"] == "gpt-6.1-sol"
    assert flow.invocation(restarted)["system_version_id"] == activated_hash
    flow.versions.rollback(flow.pid, flow.baseline, "v1")
    rolled_back = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.transport.calls[-1]["body"]["model"] == "gpt-6-luna"
    assert flow.invocation(rolled_back)["system_version_id"] == flow.baseline
    assert flow.invocation(before)["system_version_id"] == flow.baseline
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 8


def test_engineer_unapproved_routing_is_failed_with_independent_evidence_and_expense(tmp_path):
    flow = RuntimeFlow(tmp_path)
    proposal = _task(flow.clock, flow.pid, flow.baseline, max_steps=1,
        max_spend=Money(amount="0.5", currency="EUR"), allowed_classes=["approved_model_routing"],
        allowed_paths=["artifacts/model_routing.json"])
    flow.engineer.propose(flow.pid, proposal)
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "commission", "change_id": proposal.record_id}])
    flow.add("leader")
    assert flow.run("leader") == 1
    flow.transport.outputs["EngineerPatch"] = {"summary": "Attempt an unapproved route.", "files": [
        {"path": "artifacts/model_routing.json",
         "content": json.dumps({"schema_version": 1, "routes": {"trader": "unapproved"}})}]}
    assert flow.run("engineer") == 1
    engineering = flow.db.execute("SELECT * FROM tasks WHERE role = 'engineer'").fetchone()
    assert engineering["status"] == "FAILED"
    candidate = flow.db.execute("SELECT * FROM candidates").fetchone()
    assert candidate["state"] == "FAILED"
    attestation = flow.db.execute("SELECT report_json FROM candidate_attestations").fetchone()
    assert "protected model registry" in attestation[0]
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts WHERE synthetic = 0").fetchone()[0] == 2


@pytest.mark.parametrize("verified,effective,model", [
    ("2025-11-30", "2025-11-30", "gpt-6-luna"),
    ("2026-01-02", "2026-01-01", "gpt-6-luna"),
    ("2026-01-01", "2026-01-02", "gpt-6-luna"),
    ("2026-01-01", "2026-01-01", "not-approved-by-capabilities"),
])
def test_runtime_rejects_stale_future_or_unsupported_price_registry(verified, effective, model, tmp_path):
    flow = RuntimeFlow(tmp_path)
    card = _card("invalid", model=model).model_copy(update={"verified_at": verified, "effective_at": effective})
    flow.office.budget.seed_card(card)
    flow.config = flow.config.model_copy(update={"approved_price_card_ids": ["invalid"],
                                                "role_routes": {"trader": "invalid"}})
    flow.assembly = flow.bind()
    task = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(task)["status"] == "FAILED"
    assert not flow.transport.calls
    assert not flow.assembly.gateway.attempts


def test_routing_artifact_has_no_price_secret_endpoint_or_policy_grammar(tmp_path):
    from trade_graph.adapters.engineering.artifact_policy import validate

    for extra in [{"api_key": "private"}, {"paid_calls_enabled": True}, {"fx_rate": "0"},
                  {"price_cards": []}, {"endpoint": "https://example.org"}]:
        assert validate({"artifacts/context_policy.json": json.dumps(POLICY),
            "artifacts/model_routing.json": json.dumps({"schema_version": 1, "routes": {"trader": "primary"},
                                                        **extra})})


def test_scripted_runtime_records_only_synthetic_expense(tmp_path):
    flow = RuntimeFlow(tmp_path, provider="scripted", paid=False, owner_paid=False, keys={})
    flow.assembly.gateway.scripted.outputs["trader"] = _choice()
    task = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(task)["status"] == "SUCCEEDED"
    assert not flow.transport.calls
    assert flow.office.budget.remaining("deployment") == Decimal("5")
    assert flow.db.execute("SELECT synthetic FROM usage_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("with_key", [True, False])
def test_artifact_provider_selection_uses_actual_adapter_and_private_key_or_refuses(with_key, tmp_path):
    flow = RuntimeFlow(tmp_path, routing={"schema_version": 1, "routes": {"trader": "anthropic-card"}})
    flow.office.budget.seed_card(_card("anthropic-card", provider="anthropic", model="claude-sonnet-5-5"))
    flow.config = flow.config.model_copy(update={"approved_price_card_ids": ["primary", "anthropic-card"]})
    if with_key:
        flow.keys["anthropic"] = "synthetic-second-private-credential"
    flow.assembly = flow.bind()
    task = flow.add("trader")
    assert flow.run("trader") == 1
    if with_key:
        assert flow.row(task)["status"] == "SUCCEEDED", flow.row(task)["output_json"]
        call = flow.transport.calls[-1]
        assert call["url"] == "https://api.anthropic.com/v1/messages"
        assert call["body"]["model"] == "claude-sonnet-5-5"
        assert call["headers"]["x-api-key"] == "synthetic-second-private-credential"
        assert "synthetic-second-private-credential" not in json.dumps(call["body"])
        receipt = flow.db.execute("SELECT provider,model,synthetic FROM usage_receipts").fetchone()
        assert tuple(receipt) == ("anthropic", "claude-sonnet-5-5", 0)
    else:
        assert flow.row(task)["status"] == "FAILED"
        assert not flow.transport.calls


def test_provider_unpriced_resolved_model_usage_stays_unresolved(tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.transport.resolved_model = "unapproved-provider-model"
    task = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(task)["status"] == "WAITING_EXTERNAL"
    invocation = flow.invocation(task)
    assert invocation["state"] == "UNCERTAIN"
    result = json.loads(invocation["result_json"])
    assert result["usage"]["uncached_input_tokens"] == 10
    assert result["provider_model"] == "unapproved-provider-model"
    assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert flow.office.budget.remaining("deployment") < Decimal("5")


@pytest.mark.parametrize("interrupted", [True, False])
def test_runtime_engineer_unknown_usage_or_crash_does_not_replay(interrupted, tmp_path):
    flow = RuntimeFlow(tmp_path)
    proposal = _task(flow.clock, flow.pid, flow.baseline, max_steps=1,
                     max_spend=Money(amount="0.5", currency="EUR"))
    flow.engineer.propose(flow.pid, proposal)
    flow.transport.outputs["LeaderReply"] = lambda context: reply(context["evidence_refs"], [
        {"kind": "commission", "change_id": proposal.record_id}])
    flow.add("leader")
    assert flow.run("leader") == 1
    flow.transport.outputs["EngineerPatch"] = {"summary": "Bounded policy change.", "files": [
        {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)}]}
    flow.transport.interrupt = interrupted
    flow.transport.omit_usage = True
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            flow.run("engineer")
    else:
        assert flow.run("engineer") == 1
    engineering = flow.db.execute("SELECT * FROM tasks WHERE role = 'engineer'").fetchone()
    flow.expire(engineering["task_id"])
    if not interrupted:
        flow.db.execute("UPDATE tasks SET status = 'QUEUED' WHERE task_id = ?", (engineering["task_id"],))
    flow.assembly = flow.bind()
    assert flow.run("engineer") == 1
    assert len(flow.transport.calls) == 2
    assert flow.row(engineering["task_id"])["status"] == "WAITING_EXTERNAL"
    assert flow.invocation(engineering["task_id"])["state"] == "UNCERTAIN"
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_current_owner_policy_revokes_paid_dispatch_without_factory_restart(tmp_path):
    flow = RuntimeFlow(tmp_path)
    allowed = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(allowed)["status"] == "SUCCEEDED"
    flow.office.execution.authority.install_policy(paper_owner_policy(revision_id="paid-revoked"), role="owner")
    denied = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(denied)["status"] == "FAILED"
    assert "owner has not enabled paid calls" in flow.row(denied)["output_json"]
    assert len(flow.transport.calls) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_task_inputs_cannot_inject_provider_fixtures_or_synthetic_billing(tmp_path):
    flow = RuntimeFlow(tmp_path)
    task = flow.add("trader", payload={"http_fixture": {"usage": {"input_tokens": 0}},
        "scripted_result": _choice(), "api_keys": {"openai": "injected-secret"}, "synthetic": True})
    assert flow.run("trader") == 1
    assert flow.row(task)["status"] == "SUCCEEDED"
    assert len(flow.transport.calls) == 1
    body = json.dumps(flow.transport.calls[0]["body"])
    assert "http_fixture" not in body and "injected-secret" not in body
    assert flow.db.execute("SELECT synthetic FROM usage_receipts").fetchone()[0] == 0
