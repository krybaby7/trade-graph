"""Real commissioned Engineer work: bounded generation, billing, fencing and recovery."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.integration.test_engineer import POLICY, _stack, _task
from tests.leadership_support import commission

from trade_graph.application.authority import paper_owner_policy
from trade_graph.application.engineering_workflow import EngineerHandler
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import ModelResult, ModelUsage
from trade_graph.domain.errors import StaleState
from trade_graph.domain.money import Money


@dataclass
class Flow:
    clock: object
    db: object
    pid: str
    source: Path
    engineer: object
    versions: object
    baseline: str
    office: object
    secretary: object
    gateway: object
    change_id: str
    tid: str
    root: str
    workspace: Path
    handler: object
    worker: object

    def row(self):
        return self.db.execute("SELECT * FROM tasks WHERE task_id = ?", (self.tid,)).fetchone()

    def output(self):
        return json.loads(self.row()["output_json"])

    def receipts(self):
        return self.db.execute(
            """SELECT r.*, b.task_id, b.root_task_id, b.attempt_kind FROM usage_receipts r
            JOIN budget_reservations b USING (reservation_id) WHERE b.role = 'engineer'"""
        ).fetchall()

    def run(self):
        return self.worker.run_available({"engineer": self.handler})

    def restart(self, monkeypatch):
        gateway = ModelGateway(self.gateway.budget, paid_calls_enabled=False)
        monkeypatch.setattr(gateway.scripted, "complete", lambda _: pytest.fail("replayed model attempt"))
        self.handler = EngineerHandler(
            self.engineer,
            self.office.scheduler,
            gateway,
            deployment_id="deployment",
            price_card_id="scripted-review",
            workspace_root=self.workspace,
        )
        self.gateway = gateway


def _flow(tmp_path, *, max_steps=3, max_spend="1"):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    (source / "artifacts" / "context_policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
    (source / "private.sqlite").write_bytes(b"PRIVATE DATABASE CONTENT")
    baseline = engineer.baseline(tmp_path / "baseline")
    versions.ensure(pid, "v1", baseline)
    proposal = _task(clock, pid, baseline, max_steps=max_steps, max_spend=Money(amount=max_spend, currency="EUR"))
    office, secretary, gateway, _, root = commission(engineer, pid, proposal)
    tid = db.execute("SELECT worker_task_id FROM engineering_commissions").fetchone()[0]
    workspace = tmp_path / "engineering-work"
    handler = EngineerHandler(
        engineer,
        office.scheduler,
        gateway,
        deployment_id="deployment",
        price_card_id="scripted-review",
        workspace_root=workspace,
    )
    worker = RoleWorker(office.scheduler, owner="fixture-worker", system_version_id=baseline, reconcile=lambda: None)
    gateway.attempts.clear()
    gateway.scripted.outputs["engineer"] = patch()
    return Flow(
        clock,
        db,
        pid,
        source,
        engineer,
        versions,
        baseline,
        office,
        secretary,
        gateway,
        proposal.record_id,
        tid,
        root,
        workspace,
        handler,
        worker,
    )


@pytest.fixture
def flow(tmp_path):
    return _flow(tmp_path)


def patch(policy=None):
    return {
        "files": [
            {
                "path": "artifacts/context_policy.json",
                "content": json.dumps(policy or {**POLICY, "max_general_lessons": 3}),
            }
        ],
        "summary": "Bound the number of lessons while retaining safety and mandate context.",
    }


def finish_bounded(flow):
    """Allow documented delayed repairs; never accidentally spin on queued work."""
    for _ in range(4):
        assert flow.run() == 1
        if flow.row()["status"] != "QUEUED":
            return flow.output()
        assert flow.run() == 0
        flow.clock.advance(31)
    pytest.fail("Engineer exceeded its commissioned attempt bound")


def expire_task_lease(flow):
    expires = datetime.fromisoformat(flow.row()["lease_expires_at"].replace("Z", "+00:00"))
    flow.clock.advance(int((expires - flow.clock.now()).total_seconds()) + 1)


def test_generated_artifact_has_independent_attestation_and_retained_cost(flow):
    assert flow.run() == 1
    output = flow.output()
    assert output["_status"] == "SUCCEEDED"
    assert output["change_id"] == flow.change_id
    candidate = flow.db.execute("SELECT * FROM candidates WHERE candidate_id = ?", (output["candidate_id"],)).fetchone()
    assert candidate["state"] == "READY"
    assert candidate["baseline_hash"] == flow.baseline
    assert candidate["content_hash"] != flow.baseline
    attestation = flow.db.execute(
        "SELECT * FROM candidate_attestations WHERE candidate_id = ?", (output["candidate_id"],)
    ).fetchone()
    report = json.loads(attestation["report_json"])
    assert attestation["exit_code"] == 0
    assert report["isolation"]["verified"] is True
    assert json.loads(report["artifact_files"]["artifacts/context_policy.json"])["max_general_lessons"] == 3
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert output["candidate_id"] in output["evidence_refs"]
    result = flow.db.execute("SELECT * FROM role_results WHERE task_id = ?", (flow.tid,)).fetchone()
    assert json.loads(result["document_json"])["candidate_id"] == output["candidate_id"]
    report_row = flow.db.execute("SELECT document_json FROM secretary_reports WHERE role = 'engineer'").fetchone()
    assert output["candidate_id"] in json.loads(report_row[0])["evidence_refs"]
    receipts = flow.receipts()
    assert len(receipts) == len(flow.gateway.attempts) == 1
    assert receipts[0]["synthetic"] == 1
    assert (receipts[0]["task_id"], receipts[0]["root_task_id"]) == (flow.tid, flow.root)
    allocation = flow.db.execute(
        "SELECT * FROM cost_allocations WHERE receipt_id = ?", (receipts[0]["receipt_id"],)
    ).fetchone()
    assert allocation["portfolio_id"] == flow.pid and Decimal(allocation["amount"]) > 0
    assert flow.office.budget.remaining("deployment") == Decimal("5")
    assert flow.row()["attempts_used"] == 1
    assert flow.run() == 0


def test_request_and_dispatch_are_durable_before_provider_receives_work(flow, monkeypatch):
    complete = flow.gateway.scripted.complete

    def inspect(request):
        job = flow.db.execute("SELECT * FROM engineering_jobs WHERE task_id = ?", (flow.tid,)).fetchone()
        assert job is not None and job["attempt_id"]
        invocation = flow.db.execute(
            "SELECT * FROM model_invocations WHERE invocation_id = ?", (job["attempt_id"],)
        ).fetchone()
        assert invocation["state"] == "DISPATCHED"
        assert json.loads(invocation["request_json"]) == request.model_dump(mode="json")
        assert request.max_tool_calls == 0
        assert request.task_id == flow.tid and request.root_task_id == flow.root
        assert flow.row()["attempts_used"] == 1
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", inspect)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED"


def test_source_context_contains_only_bounded_allowlisted_artifact_data(flow, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-fixture")
    complete = flow.gateway.scripted.complete

    def inspect(request):
        serialized = request.model_dump_json()
        for secret in (
            "sk-private-fixture",
            "sk-should-not-copy",
            "PRIVATE DATABASE CONTENT",
            "secret = 1",
            ".env",
            "private.sqlite",
        ):
            assert secret not in serialized
        assert "artifacts/context_policy.json" in serialized
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", inspect)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED"
    assert not list(flow.workspace.rglob(".env"))
    assert not list(flow.workspace.rglob("*.sqlite"))
    assert not list(flow.workspace.rglob("books.py"))


def test_schema_repair_waits_and_accounts_for_both_attempts(flow):
    flow.gateway.scripted.outputs["engineer"] = {"advice": "reduce the context"}
    assert flow.run() == 1
    assert flow.row()["status"] == "QUEUED"
    assert flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == 1
    assert flow.run() == 0
    flow.clock.advance(29)
    assert flow.run() == 0
    flow.gateway.scripted.outputs["engineer"] = patch()
    flow.clock.advance(1)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED"
    assert flow.row()["attempts_used"] == 2
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 2
    assert {r["attempt_kind"] for r in flow.receipts()} == {"primary", "schema_repair"}
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'READY'").fetchone()[0] == 1


def test_schema_repair_respects_one_attempt_commission(tmp_path):
    flow = _flow(tmp_path, max_steps=1)
    flow.gateway.scripted.outputs["engineer"] = {"advice": "reduce the context"}
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert flow.row()["attempts_used"] == 1
    flow.clock.advance(31)
    assert flow.run() == 0
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 1


def test_failed_trusted_checks_retain_failed_candidate_and_bill(flow):
    flow.gateway.scripted.outputs["engineer"] = patch({"max_general_lessons": 3, "always_include": []})
    output = finish_bounded(flow)
    assert output["_status"] == "FAILED"
    candidate = flow.db.execute("SELECT * FROM candidates WHERE candidate_id = ?", (output["candidate_id"],)).fetchone()
    assert candidate["state"] == "FAILED"
    attestation = flow.db.execute(
        "SELECT exit_code FROM candidate_attestations WHERE candidate_id = ?", (output["candidate_id"],)
    ).fetchone()
    assert attestation["exit_code"] != 0
    assert len(flow.receipts()) == len(flow.gateway.attempts) >= 1
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'READY'").fetchone()[0] == 0


def test_failed_check_findings_drive_delayed_patch_repair_and_keep_both_costs(flow, monkeypatch):
    failed_patch = patch({**POLICY, "max_general_lessons": 3, "always_include": []})
    flow.gateway.scripted.outputs["engineer"] = failed_patch
    assert flow.run() == 1
    assert flow.row()["status"] == "QUEUED" and flow.row()["attempts_used"] == 1
    failed = flow.db.execute("SELECT candidate_id FROM candidates WHERE state = 'FAILED'").fetchone()[0]
    assert flow.run() == 0
    complete = flow.gateway.scripted.complete

    def inspect_repair(request):
        findings = request.context["previous_findings"]
        finding = next(f for f in findings if f.get("candidate_id") == failed)
        assert finding["state"] == "FAILED"
        assert any("required lessons missing" in error for error in finding["check_failures"])
        assert "required lessons missing" in finding["reason"]
        assert request.context["previous_patch"] == {
            "artifacts/context_policy.json": failed_patch["files"][0]["content"]
        }
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", inspect_repair)
    flow.gateway.scripted.outputs["engineer"] = patch()
    flow.clock.advance(30)
    assert flow.run() == 1
    output = flow.output()
    assert output["_status"] == "SUCCEEDED"
    assert output["candidate_id"] != failed
    assert {output["candidate_id"], failed}.issubset(output["evidence_refs"])
    assert flow.row()["attempts_used"] == 2
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 2
    assert {r["attempt_kind"] for r in flow.receipts()} == {"primary", "patch_repair"}
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2
    assert flow.versions.current_hash(flow.pid) == flow.baseline


@pytest.mark.parametrize(
    "payload",
    [
        {"files": [], "summary": "Only advice, no implementation."},
        {
            "files": [{"path": "src/trade_graph/kernel/books.py", "content": "cash = 1"}],
            "summary": "Modify accounting.",
        },
        {"files": [{"path": "tests/test_gates.py", "content": "assert True"}], "summary": "Replace acceptance gates."},
        {
            "files": [
                {"path": "artifacts/context_policy.json", "content": "{}"},
                {"path": "artifacts/context_policy.json", "content": json.dumps(POLICY)},
            ],
            "summary": "Duplicate paths.",
        },
    ],
)
def test_advice_protected_paths_and_duplicates_cannot_produce_ready_candidate(flow, payload):
    flow.gateway.scripted.outputs["engineer"] = payload
    output = finish_bounded(flow)
    assert output["_status"] == "FAILED"
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'READY'").fetchone()[0] == 0
    assert (flow.source / "src" / "trade_graph" / "kernel" / "books.py").read_text() == "secret = 1\n"
    assert len(flow.receipts()) == len(flow.gateway.attempts) >= 1


def test_crash_after_durable_response_recovers_without_new_model_attempt(flow, monkeypatch):
    implement = flow.engineer.implement

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("before apply")

    monkeypatch.setattr(flow.engineer, "implement", crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    monkeypatch.setattr(flow.engineer, "implement", implement)
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED"
    assert flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1


def test_crash_after_candidate_commit_recovers_idempotently_at_attempt_limit(tmp_path, monkeypatch):
    flow = _flow(tmp_path, max_steps=1)
    implement = flow.engineer.implement

    def commit_then_crash(*args, **kwargs):
        implement(*args, **kwargs)
        raise KeyboardInterrupt("candidate committed before job result")

    monkeypatch.setattr(flow.engineer, "implement", commit_then_crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    original = flow.db.execute("SELECT candidate_id FROM candidates").fetchone()[0]
    monkeypatch.setattr(flow.engineer, "implement", implement)
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.output()["candidate_id"] == original
    assert flow.row()["status"] == "SUCCEEDED"
    assert flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidate_attestations").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM secretary_reports WHERE role = 'engineer'").fetchone()[0] == 1


def test_local_validation_crash_recovers_in_new_directory_without_new_model_or_attempt(tmp_path, monkeypatch):
    flow = _flow(tmp_path, max_steps=1)
    attest = flow.engineer.runner.attest

    def crash(destination):
        assert (destination / "artifacts" / "context_policy.json").is_file()
        raise KeyboardInterrupt("during independent local checks")

    monkeypatch.setattr(flow.engineer.runner, "attest", crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    retained = list(flow.workspace.iterdir())
    assert len(retained) == 1
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    monkeypatch.setattr(flow.engineer.runner, "attest", attest)
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED" and flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == 1
    assert len(list(flow.workspace.iterdir())) == 2 and retained[0].is_dir()
    assert flow.db.execute("SELECT attempts_used FROM change_tasks").fetchone()[0] == 1
    attempt = flow.db.execute("SELECT details_json FROM engineering_attempts").fetchone()[0]
    assert json.loads(attempt)["recoveries"][0]["destination"] == str(retained[0])
    assert flow.db.execute("SELECT COUNT(*) FROM engineering_attempts").fetchone()[0] == 1


def test_repeated_local_check_crashes_stop_after_three_recoveries_with_one_bill(tmp_path, monkeypatch):
    flow = _flow(tmp_path, max_steps=1)

    def crash(destination):
        raise KeyboardInterrupt("local checker crashed")

    monkeypatch.setattr(flow.engineer.runner, "attest", crash)
    for _ in range(4):
        with pytest.raises(KeyboardInterrupt):
            flow.run()
        expire_task_lease(flow)
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert "local validation recovery limit" in flow.output()["reason"]
    assert flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 1
    assert len(list(flow.workspace.iterdir())) == 4
    attempt = flow.db.execute("SELECT details_json FROM engineering_attempts").fetchone()[0]
    assert len(json.loads(attempt)["recoveries"]) == 3
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert flow.run() == 0


def test_crash_before_worker_finish_recovers_terminal_report_once(flow, monkeypatch):
    finish = flow.office.scheduler.finish

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("before scheduler finish")

    monkeypatch.setattr(flow.office.scheduler, "finish", crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    original = flow.db.execute("SELECT document_json FROM role_results WHERE task_id = ?", (flow.tid,)).fetchone()[0]
    monkeypatch.setattr(flow.office.scheduler, "finish", finish)
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.output() == json.loads(original)
    assert flow.row()["attempts_used"] == 1
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM secretary_reports WHERE role = 'engineer'").fetchone()[0] == 1


def test_lost_response_recovery_keeps_uncertain_hold_and_waits_without_retry(flow, monkeypatch):
    def lost_response(_):
        raise KeyboardInterrupt("after dispatch, before receipt")

    monkeypatch.setattr(flow.gateway.scripted, "complete", lost_response)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    assert not flow.receipts()
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.row()["status"] == "WAITING_EXTERNAL"
    assert flow.row()["attempts_used"] == 1
    hold = flow.db.execute("SELECT * FROM budget_reservations WHERE role = 'engineer'").fetchone()
    assert hold["state"] == "UNCERTAIN" and Decimal(hold["amount"]) > 0
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    flow.clock.advance(3600)
    assert flow.run() == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE role = 'engineer'").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["owner_halt", "commission_revoked"])
def test_lost_response_still_recovers_billing_hold_after_authority_revocation(flow, monkeypatch, fault):
    def lost_response(_):
        raise KeyboardInterrupt("after dispatch, before receipt")

    monkeypatch.setattr(flow.gateway.scripted, "complete", lost_response)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    if fault == "owner_halt":
        flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "halt before recovery")
    else:
        flow.db.execute("UPDATE engineering_commissions SET state = 'REVOKED' WHERE change_id = ?", (flow.change_id,))
    expire_task_lease(flow)
    flow.restart(monkeypatch)
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    hold = flow.db.execute("SELECT * FROM budget_reservations WHERE role = 'engineer'").fetchone()
    assert hold["state"] == "UNCERTAIN" and Decimal(hold["amount"]) > 0
    assert flow.db.execute("SELECT state FROM model_invocations").fetchone()[0] == "UNCERTAIN"
    assert not flow.receipts()
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE role = 'engineer'").fetchone()[0] == 1
    assert flow.run() == 0


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "anthropic", "model": "claude-sonnet-5-5"},
        {"price_card_id": "different-card"},
        {"fx_buffer": Decimal("1.10")},
    ],
)
def test_changed_handler_configuration_cannot_dispatch_prepared_request(flow, monkeypatch, config):
    def crash(*args, **kwargs):
        raise KeyboardInterrupt("request prepared, no reservation or dispatch")

    monkeypatch.setattr(flow.gateway, "invoke", crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    job = flow.db.execute("SELECT document_json FROM engineering_jobs").fetchone()[0]
    assert json.loads(job)["phase"] == "REQUESTING"
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    expire_task_lease(flow)
    gateway = ModelGateway(flow.gateway.budget, paid_calls_enabled=False)
    monkeypatch.setattr(gateway.scripted, "complete", lambda _: pytest.fail("configuration change dispatched work"))
    kwargs = {"deployment_id": "deployment", "price_card_id": "scripted-review", "workspace_root": flow.workspace}
    flow.handler = EngineerHandler(flow.engineer, flow.office.scheduler, gateway, **{**kwargs, **config})
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert flow.row()["attempts_used"] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE role = 'engineer'").fetchone()[0] == 0
    assert not flow.receipts()
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_rate_limit_defers_then_retries_with_separate_receipts(flow, monkeypatch):
    complete = flow.gateway.scripted.complete
    monkeypatch.setattr(
        flow.gateway.scripted,
        "complete",
        lambda _: ModelResult(
            ok=False,
            failure="rate_limit",
            message="retry later",
            usage=ModelUsage(uncached_input_tokens=2, billed_output_tokens=0),
        ),
    )
    assert flow.run() == 1
    assert flow.row()["status"] == "QUEUED" and flow.row()["attempts_used"] == 1
    assert flow.run() == 0
    assert len(flow.receipts()) == 1
    monkeypatch.setattr(flow.gateway.scripted, "complete", complete)
    flow.clock.advance(30)
    assert flow.run() == 1
    assert flow.row()["status"] == "SUCCEEDED" and flow.row()["attempts_used"] == 2
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 2


def test_repeated_rate_limits_stop_at_commissioned_attempt_limit(flow, monkeypatch):
    monkeypatch.setattr(
        flow.gateway.scripted,
        "complete",
        lambda _: ModelResult(
            ok=False,
            failure="rate_limit",
            message="retry later",
            usage=ModelUsage(uncached_input_tokens=2, billed_output_tokens=0),
        ),
    )
    output = finish_bounded(flow)
    assert output["_status"] == "FAILED"
    assert flow.row()["attempts_used"] == 3
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 3
    flow.clock.advance(3600)
    assert flow.run() == 0


@pytest.mark.parametrize("fault", ["policy", "owner_halt"])
def test_authority_revoked_during_provider_call_retains_bill_without_ready_candidate(flow, monkeypatch, fault):
    complete = flow.gateway.scripted.complete

    def revoke(request):
        result = complete(request)
        if fault == "policy":
            flow.office.execution.authority.install_policy(paper_owner_policy(revision_id="2"), role="owner")
        else:
            flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "halt during inference")
        return result

    monkeypatch.setattr(flow.gateway.scripted, "complete", revoke)
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert len(flow.receipts()) == len(flow.gateway.attempts) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'READY'").fetchone()[0] == 0
    assert flow.versions.current_hash(flow.pid) == flow.baseline


@pytest.mark.parametrize("boundary", ["provider", "checks"])
def test_expired_scheduler_lease_cannot_publish_but_retains_receipt(flow, monkeypatch, boundary):
    if boundary == "provider":
        complete = flow.gateway.scripted.complete

        def expire(request):
            result = complete(request)
            expire_task_lease(flow)
            return result

        monkeypatch.setattr(flow.gateway.scripted, "complete", expire)
    else:
        attest = flow.engineer.runner.attest

        def expire(destination):
            report = attest(destination)
            expire_task_lease(flow)
            return report

        monkeypatch.setattr(flow.engineer.runner, "attest", expire)
    with pytest.raises(StaleState):
        flow.run()
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM role_results WHERE task_id = ?", (flow.tid,)).fetchone()[0] == 0
    assert flow.versions.current_hash(flow.pid) == flow.baseline


@pytest.mark.parametrize("boundary", ["provider", "checks"])
def test_replaced_scheduler_token_cannot_publish_but_retains_receipt(flow, monkeypatch, boundary):
    def replace():
        flow.db.execute("UPDATE tasks SET lease_token = 'replacement-token' WHERE task_id = ?", (flow.tid,))

    if boundary == "provider":
        complete = flow.gateway.scripted.complete

        def replaced(request):
            result = complete(request)
            replace()
            return result

        monkeypatch.setattr(flow.gateway.scripted, "complete", replaced)
    else:
        attest = flow.engineer.runner.attest

        def replaced(destination):
            result = attest(destination)
            replace()
            return result

        monkeypatch.setattr(flow.engineer.runner, "attest", replaced)
    with pytest.raises(StaleState):
        flow.run()
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM role_results WHERE task_id = ?", (flow.tid,)).fetchone()[0] == 0


def test_source_baseline_changed_during_checks_cannot_produce_ready_candidate(flow, monkeypatch):
    attest = flow.engineer.runner.attest

    def drift(destination):
        result = attest(destination)
        (flow.source / "artifacts" / "context_policy.json").write_text(
            json.dumps({**POLICY, "max_general_lessons": 4}), encoding="utf-8"
        )
        return result

    monkeypatch.setattr(flow.engineer.runner, "attest", drift)
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'READY'").fetchone()[0] == 0
    assert flow.versions.current_hash(flow.pid) == flow.baseline


@pytest.mark.parametrize("fault", ["policy", "owner_halt", "baseline", "source_baseline"])
def test_current_authority_or_budget_denies_generation_before_model_call(flow, fault):
    if fault == "policy":
        flow.office.execution.authority.install_policy(paper_owner_policy(revision_id="2"), role="owner")
    elif fault == "owner_halt":
        flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "halt engineering")
    elif fault == "baseline":
        flow.db.execute("UPDATE active_versions SET artifact_hash = 'new'")
    elif fault == "source_baseline":
        (flow.source / "artifacts" / "context_policy.json").write_text(
            json.dumps({**POLICY, "max_general_lessons": 4}), encoding="utf-8"
        )
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert flow.gateway.attempts == []
    assert not flow.receipts()
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_zero_spend_commission_is_blocked_before_provider_dispatch(tmp_path):
    flow = _flow(tmp_path, max_spend="0")
    assert flow.run() == 1
    assert flow.row()["status"] == "BLOCKED_BUDGET"
    assert flow.gateway.attempts == []
    assert not flow.receipts()
    assert flow.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
