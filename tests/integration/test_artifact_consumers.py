"""Activated bytes reach real Trader inference; recovery never replays a paid effect."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest
from tests.integration.test_engineer import POLICY, _stack, _task
from tests.integration.test_learning import _lesson
from tests.leadership_support import commission

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import ObservationPolicy, VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.authority import paper_owner_policy, seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.engineering_workflow import EngineerHandler
from trade_graph.application.execution import Execution
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.leader import LeaderOffice, Secretary
from trade_graph.application.learning import LearningJournal
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.trader_workflow import TraderHandler
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import InstrumentRules, ModelResult, Observation
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

STRATEGY_PATH = "strategies/templates/range_reversion.json"
BASE_PROMPT = "Use the pinned snapshot. Baseline Trader instructions."
NEW_PROMPT = "Use the pinned snapshot. Activated Trader instructions."
BASE_STRATEGY = {
    "strategy_id": "range-reversion",
    "status": "unproven",
    "features": ["range_high", "range_low", "midpoint"],
    "entry": "Baseline range entry from the visible quote.",
    "exit": "Range midpoint or invalidation.",
    "sizing_envelope": "Inside the protected mandate.",
    "invalidation": "Range breaks.",
    "execution": "Fresh quote and shared execution controls.",
    "costs": "Conservative paper fee assumption.",
    "validation": "Forward paper observation.",
}
NEW_STRATEGY = {**BASE_STRATEGY, "entry": "Activated range entry from the visible quote."}


def _choice(action="hold"):
    return {
        "action": action,
        "symbol": "BTC/USD" if action == "enter" else None,
        "quantity": "0.01" if action == "enter" else None,
        "strategy_id": "range-reversion",
        "rationale": "Synthetic discretionary decision from activated context.",
        "invalidation": "The visible range breaks.",
        "experiment": False,
        "evidence_ids": [],
        "no_action_reason": "No entry at this snapshot." if action == "hold" else None,
    }


@dataclass
class ConsumerFlow:
    clock: object
    db: Database
    pid: str
    baseline: str
    candidate_id: str
    office: LeaderOffice
    secretary: Secretary
    gateway: ModelGateway
    versions: VersionController
    runtime: ArtifactRuntime | None = None
    handler: TraderHandler | None = None
    worker: RoleWorker | None = None

    def bind_consumer(self):
        self.runtime = ArtifactRuntime(self.versions, self.office.scheduler)
        self.handler = TraderHandler(
            self.office,
            self.secretary,
            self.gateway,
            artifact_runtime=self.runtime,
            deployment_id="deployment",
            price_card_id="scripted-review",
            provider="scripted",
            model="scripted",
        )
        self.worker = RoleWorker(
            self.office.scheduler,
            owner="fixture-worker",
            system_version_id=self.baseline,
            reconcile=lambda: asyncio.run(self.office.execution.reconcile()),
            artifact_runtime=self.runtime,
        )

    def add_turn(self):
        return self.office.scheduler.add_task(
            role="trader",
            objective="Decide from the current protected snapshot.",
            portfolio_id=self.pid,
            payload={"symbol": "BTC/USD"},
            expected_version=self.versions.current_hash(self.pid),
            max_attempts=1,
            allocated_spend=Decimal("1"),
        )

    def run(self):
        return self.worker.run_available({"trader": self.handler})

    def row(self, task_id):
        return self.db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()

    def receipts(self, task_id):
        return self.db.execute(
            """SELECT r.*, b.system_version_id, b.task_id, b.root_task_id FROM usage_receipts r
            JOIN budget_reservations b USING (reservation_id) WHERE b.task_id = ?""",
            (task_id,),
        ).fetchall()

    def activate(self):
        self.versions.activate(self.pid, {"candidate_id": self.candidate_id})

    def expire(self, task_id):
        expiry = datetime.fromisoformat(self.row(task_id)["lease_expires_at"].replace("Z", "+00:00"))
        self.clock.advance(int((expiry - self.clock.now()).total_seconds()) + 1)

    def reopen(self):
        path = self.db.path
        self.db.close()
        self.db = Database(path)
        ledger = Ledger(self.db, self.clock)
        execution = Execution(self.db, ledger, self.clock, PaperBroker(self.db, self.clock))
        scheduler = Scheduler(self.db, self.clock)
        budget = BudgetGateway(self.db, self.clock)
        self.office = LeaderOffice(execution, scheduler, budget)
        self.secretary = Secretary(execution, scheduler)
        self.gateway = ModelGateway(budget, paid_calls_enabled=False)
        self.versions = VersionController(self.db, self.clock)
        self.bind_consumer()


def _consumer_flow(tmp_path, *, activate=True, observation_policy=None, schedule=False, new_prompt=NEW_PROMPT):
    clock, db, pid, source, engineer, versions = _stack(tmp_path)
    files = {
        "artifacts/context_policy.json": json.dumps(POLICY),
        "prompts/trader.md": BASE_PROMPT,
        STRATEGY_PATH: json.dumps(BASE_STRATEGY),
    }
    if schedule:
        files["artifacts/schedules.json"] = json.dumps({"schema_version": 1, "interval_seconds": {"trader": 60}})
    for name, content in files.items():
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    baseline = engineer.baseline(tmp_path / "baseline")
    versions.ensure(pid, "v1", baseline)
    versions.register_baseline(pid, "v1", engineer.runner.source_files())
    authority = seed_paper_authority(db, clock, pid)
    classes = ["artifact_config", "context_policy", "prompt"]
    if schedule:
        classes.append("schedule")
    authority.install_policy(
        paper_owner_policy(revision_id="2").model_copy(update={"allowed_change_classes": classes}),
        role="owner",
    )
    proposal = _task(clock, pid, baseline, allowed_classes=classes, allowed_paths=list(files), max_steps=1)
    office, secretary, gateway, _leader, _root = commission(engineer, pid, proposal)
    changed = {
        "artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 3}),
        "prompts/trader.md": new_prompt,
        STRATEGY_PATH: json.dumps(NEW_STRATEGY),
    }
    if schedule:
        changed["artifacts/schedules.json"] = json.dumps({"schema_version": 1, "interval_seconds": {"trader": 10}})
    gateway.scripted.outputs["engineer"] = {
        "files": [{"path": name, "content": content} for name, content in changed.items()],
        "summary": "Use bounded context and updated unproven Trader guidance.",
    }
    engineer_handler = EngineerHandler(
        engineer,
        office.scheduler,
        gateway,
        deployment_id="deployment",
        price_card_id="scripted-review",
        workspace_root=tmp_path / "engineering-work",
    )
    worker = RoleWorker(
        office.scheduler, owner="fixture-worker", system_version_id=baseline, reconcile=lambda: None
    )
    assert worker.run_available({"engineer": engineer_handler}) == 1
    candidate = db.execute("SELECT * FROM candidates WHERE state = 'READY'").fetchone()
    assert candidate is not None
    flow = ConsumerFlow(clock, db, pid, baseline, candidate["candidate_id"], office, secretary, gateway, versions)
    if observation_policy is not None:
        versions.observation_policy = observation_policy
    if activate:
        flow.activate()
    office.execution.ledger.deposit(pid, "USD", Decimal("10000"), "opening-funds")
    office.execution.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.9"), source="fixture", kind="fixture", stale=False
    )
    office.execution.register_instrument(
        InstrumentRules(
            venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
            price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
            min_notional="1", synthetic=True,
        )
    )
    office.execution.save_observation(
        Observation(
            observation_id="visible-quote", venue="paper", symbol="BTC/USD", event_time_utc=clock.now(),
            available_at_utc=clock.now(), bid="99", ask="100", bid_size="1", ask_size="1",
            kind="quote", source="fixture",
        )
    )
    journal = LearningJournal(db, clock)
    for index in range(9):
        journal.append(
            pid,
            _lesson(
                clock, pid, record_id=f"general-{index}-r1", lesson_id=f"general-{index}",
                observation=f"Synthetic general lesson {index}.", system_version_id=baseline,
            ),
        )
    # Latest revision must replace its predecessor in inference context.
    journal.append(
        pid,
        _lesson(
            clock, pid, record_id="general-0-r2", lesson_id="general-0", revision=2,
            supersedes="general-0-r1", counterexamples=["later bar reversed", "synthetic new counterexample"],
            observation="Synthetic revised general lesson.", system_version_id=baseline,
        ),
    )
    gateway.scripted.outputs["trader"] = _choice()
    gateway.attempts.clear()
    flow.bind_consumer()
    return flow


def _assert_provenance(flow, task_id, request, expected_hash):
    assert request.system_version_id == expected_hash
    metadata = request.context["artifact"]
    assert metadata["artifact_hash"] == expected_hash
    assert metadata["manifest_sha256"]
    assert type(metadata["generation"]) is int
    decision = flow.db.execute("SELECT * FROM decisions WHERE decision_id = ?", (
        json.loads(flow.row(task_id)["output_json"])["decision_id"],
    )).fetchone()
    assert decision["system_version_id"] == expected_hash
    decision_document = json.loads(decision["payload_json"])
    assert decision_document["system_version_id"] == expected_hash
    assert decision_document["task_id"] == decision_document["root_task_id"] == task_id
    assert decision_document["run_id"] == request.run_id == decision["snapshot_id"]
    if decision_document["action"] == "hold":
        entry = request.context["strategy_templates"]["range-reversion"]["entry"]
        assert entry in decision_document["uncertainty_note"]
    snapshot = flow.db.execute(
        "SELECT payload_json FROM snapshots WHERE snapshot_id = ?", (decision["snapshot_id"],)
    ).fetchone()
    document = json.loads(snapshot["payload_json"])
    assert document["system_version_id"] == expected_hash
    assert document["artifact"] == metadata
    receipts = flow.receipts(task_id)
    assert len(receipts) == 1
    assert receipts[0]["system_version_id"] == expected_hash
    assert receipts[0]["task_id"] == receipts[0]["root_task_id"] == task_id
    assert receipts[0]["synthetic"] == 1
    assert Decimal(receipts[0]["reporting_cost"]) > 0
    assert flow.row(task_id)["attempts_used"] == 1


def _diagnose_failure_while_primary_turn_is_running(flow):
    """A separate real inference fails and requests rollback behind the busy turn."""
    task_id = flow.add_turn()
    gateway = ModelGateway(flow.office.budget, paid_calls_enabled=False)
    gateway.scripted.outputs["trader"] = {"action": "hold"}  # Known usage; invalid required choice fields.
    runtime = ArtifactRuntime(flow.versions, flow.office.scheduler)
    handler = TraderHandler(
        flow.office, flow.secretary, gateway, artifact_runtime=runtime,
        deployment_id="deployment", price_card_id="scripted-review", provider="scripted", model="scripted",
    )
    worker = RoleWorker(
        flow.office.scheduler, owner="fixture-worker", system_version_id=flow.baseline,
        reconcile=lambda: asyncio.run(flow.office.execution.reconcile()), artifact_runtime=runtime,
    )
    assert worker.run_available({"trader": handler}) == 1
    assert flow.row(task_id)["status"] == "FAILED"
    assert len(flow.receipts(task_id)) == 1
    assert flow.versions.maintain(flow.pid) == "ROLLBACK_PENDING"
    return task_id


def test_activation_changes_actual_trader_request_and_keeps_linked_provenance(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path, activate=False)
    requests = []
    complete = flow.gateway.scripted.complete

    def capture(request):
        requests.append(request)
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", capture)
    baseline_turn = flow.add_turn()
    assert flow.run() == 1
    assert BASE_PROMPT in requests[0].instructions
    assert len(requests[0].context["selected_context"]["lessons"]) == 5
    assert requests[0].context["strategy_templates"]["range-reversion"] == BASE_STRATEGY
    _assert_provenance(flow, baseline_turn, requests[0], flow.baseline)
    flow.activate()
    activated_hash = flow.versions.current_hash(flow.pid)
    activated_turn = flow.add_turn()
    assert flow.run() == 1
    assert NEW_PROMPT in requests[1].instructions
    assert BASE_PROMPT not in requests[1].instructions
    context = requests[1].context
    selected = context["selected_context"]
    assert len(selected["lessons"]) == 3
    assert set(selected["always_include"]) == {"mandate_obligations", "active_safety"}
    assert selected["mandate_obligations"] == context["guard"]["mandate"]
    assert selected["active_safety"] == {
        "policy": context["guard"]["policy"], "pause": context["guard"]["pause"]
    }
    assert context["strategy_templates"]["range-reversion"] == NEW_STRATEGY
    assert len({lesson["lesson_id"] for lesson in selected["lessons"]}) == 3
    assert all(lesson["record_id"] != "general-0-r1" for lesson in selected["lessons"])
    assert all(lesson["counterexamples"] for lesson in selected["lessons"])
    _assert_provenance(flow, activated_turn, requests[1], activated_hash)
    assert requests[1].context["artifact"]["generation"] > requests[0].context["artifact"]["generation"]
    assert flow.office.budget.remaining("deployment") == Decimal("5")
    output = json.loads(flow.row(activated_turn)["output_json"])
    bundle = flow.versions.load_active(flow.pid)
    assert flow.versions.observe(flow.pid, bundle, output["decision_id"], ok=True) == "OBSERVING"
    assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 1
    for index in range(2):
        task_id = flow.add_turn()
        assert flow.run() == 1
        _assert_provenance(flow, task_id, requests[index + 2], activated_hash)
        state = flow.db.execute(
            "SELECT state FROM candidates WHERE candidate_id = ?", (flow.candidate_id,)
        ).fetchone()["state"]
        assert state == ("OBSERVING" if index == 0 else "ACTIVE")
    assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 3


def test_activated_trader_schedule_dispatches_only_with_protected_budget_allocation(tmp_path):
    flow = _consumer_flow(tmp_path, schedule=True)
    task_count = flow.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    with pytest.raises(AuthorityDenied):
        flow.runtime.coalesce_due(flow.pid, "trader", allocated_spend=Decimal("99"))
    assert flow.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == task_count
    assert not flow.gateway.attempts
    task_id = flow.runtime.coalesce_due(flow.pid, "trader", allocated_spend=Decimal("0.5"))
    assert task_id is not None
    task = flow.row(task_id)
    assert Decimal(task["allocated_spend"]) == Decimal("0.5")
    assert task["max_attempts"] == 1
    assert task["expected_version"] == flow.versions.current_hash(flow.pid)
    schedule = flow.db.execute(
        "SELECT * FROM schedules WHERE portfolio_id = ? AND name = 'artifact-trader-review'", (flow.pid,)
    ).fetchone()
    assert schedule["interval_seconds"] == 10
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED"
    assert flow.row(task_id)["attempts_used"] == 1
    assert len(flow.receipts(task_id)) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert flow.runtime.coalesce_due(flow.pid, "trader", allocated_spend=Decimal("0.5")) is None


def test_input_reservation_covers_activated_long_prompt_snapshot_and_schema(tmp_path, monkeypatch):
    long_prompt = NEW_PROMPT + "\n" + "Use only point-in-time observations within the mandate. " * 240
    assert len(long_prompt.encode()) <= 16384
    flow = _consumer_flow(tmp_path, activate=False, new_prompt=long_prompt)
    complete = flow.gateway.scripted.complete
    requests, reserved_amounts = [], []

    def inspect_reservation(request):
        assert (BASE_PROMPT if not requests else long_prompt) in request.instructions
        row = flow.db.execute("SELECT payload_json FROM snapshots WHERE snapshot_id = ?", (request.run_id,)).fetchone()
        snapshot_bytes = len(json.dumps(json.loads(row["payload_json"])).encode())
        schema_bytes = len(json.dumps(request.output_schema).encode())
        instructions_bytes = len(request.instructions.encode())
        assert request.context["max_input_tokens"] >= snapshot_bytes + schema_bytes + instructions_bytes
        reservation = flow.db.execute(
            "SELECT * FROM budget_reservations WHERE task_id = ?", (request.task_id,)
        ).fetchone()
        assert reservation["state"] == "RESERVED"
        requests.append(request)
        reserved_amounts.append(Decimal(reservation["amount"]))
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", inspect_reservation)
    baseline_task = flow.add_turn()
    assert flow.run() == 1
    assert flow.row(baseline_task)["status"] == "SUCCEEDED"
    flow.activate()
    task_id = flow.add_turn()
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED"
    assert len(flow.receipts(baseline_task)) == len(flow.receipts(task_id)) == 1
    assert requests[1].context["max_input_tokens"] > requests[0].context["max_input_tokens"]
    assert reserved_amounts[1] > reserved_amounts[0]


@pytest.mark.parametrize("action", ["hold", "enter"])
def test_generation_changed_during_inference_blocks_decision_and_order_but_keeps_receipt(
    tmp_path, monkeypatch, action
):
    flow = _consumer_flow(tmp_path)
    task_id = flow.add_turn()
    active_hash = flow.versions.current_hash(flow.pid)
    complete = flow.gateway.scripted.complete
    flow.gateway.scripted.outputs["trader"] = _choice(action)

    def rollback_before_result(request):
        _diagnose_failure_while_primary_turn_is_running(flow)
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", rollback_before_result)
    try:
        flow.run()
    except StaleState:
        pass  # A controller-fenced worker may surface its stale lease to its supervisor.
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert flow.row(task_id)["status"] in {"FAILED", "CANCELLED", "DEAD_LETTER"}
    receipts = flow.receipts(task_id)
    assert len(receipts) == 1 and receipts[0]["system_version_id"] == active_hash
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()[0] == 1


def test_rollback_requested_before_dispatch_blocks_new_model_reservation(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path)
    task_id = flow.add_turn()
    invoke = flow.gateway.invoke

    def request_rollback_before_authorization(*args, **kwargs):
        _diagnose_failure_while_primary_turn_is_running(flow)
        return invoke(*args, **kwargs)

    monkeypatch.setattr(flow.gateway, "invoke", request_rollback_before_authorization)
    monkeypatch.setattr(flow.gateway.scripted, "complete", lambda _: pytest.fail("dispatched blocked generation"))
    assert flow.run() == 1
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert flow.row(task_id)["status"] == "FAILED"
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE task_id = ?", (task_id,)).fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_reconciliation_failure_cannot_be_overruled_by_late_success_observation(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path, observation_policy=ObservationPolicy(min_decisions=1, max_failures=1))
    task_id = flow.add_turn()
    # Simulate an interrupted health collector after the actual decision committed.
    monkeypatch.setattr(flow.runtime, "observe", lambda *_: None)
    assert flow.run() == 1
    output = json.loads(flow.row(task_id)["output_json"])
    bundle = flow.versions.load_active(flow.pid)

    def failed_reconciliation():
        raise RuntimeError("Synthetic recovery reconciliation failure.")

    with pytest.raises(RuntimeError, match="reconciliation failure"):
        flow.versions.begin(flow.pid, "replacement-consumer", failed_reconciliation)
    with pytest.raises((StaleState, ValidationFailure)):
        flow.versions.observe(
            flow.pid, bundle, output["decision_id"], ok=True, context_bytes=output["context_bytes"],
            input_tokens=output["input_tokens"], output_tokens=output["output_tokens"],
        )
    assert flow.versions.maintain(flow.pid) == "ROLLED_BACK"
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert len(flow.receipts(task_id)) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    candidate = flow.db.execute(
        "SELECT state FROM candidates WHERE candidate_id = ?", (flow.candidate_id,)
    ).fetchone()
    assert candidate["state"] == "ROLLED_BACK"


def test_lost_trader_response_keeps_uncertain_hold_across_database_reopen_without_replay(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path)
    task_id = flow.add_turn()

    def lost_response(request):
        dispatched = flow.db.execute(
            "SELECT * FROM model_invocations WHERE task_id = ?", (task_id,)
        ).fetchone()
        assert dispatched["state"] == "DISPATCHED"
        assert json.loads(dispatched["request_json"]) == request.model_dump(mode="json")
        raise KeyboardInterrupt("Synthetic crash after dispatch before durable response.")

    monkeypatch.setattr(flow.gateway.scripted, "complete", lost_response)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    assert not flow.receipts(task_id)
    flow.expire(task_id)
    flow.reopen()
    monkeypatch.setattr(flow.gateway.scripted, "complete", lambda _: pytest.fail("replayed uncertain Trader call"))
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "WAITING_EXTERNAL"
    assert flow.row(task_id)["attempts_used"] == 1
    reservation = flow.db.execute("SELECT * FROM budget_reservations WHERE task_id = ?", (task_id,)).fetchone()
    assert reservation["state"] == "UNCERTAIN" and Decimal(reservation["amount"]) > 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    flow.clock.advance(3600)
    assert flow.run() == 0


@pytest.mark.parametrize("action", ["hold", "enter"])
def test_successful_payload_without_usage_waits_without_a_decision_or_order(tmp_path, monkeypatch, action):
    flow = _consumer_flow(tmp_path)
    task_id = flow.add_turn()
    monkeypatch.setattr(
        flow.gateway.scripted, "complete",
        lambda _: ModelResult(ok=True, payload=_choice(action), usage=None, provider_model="scripted"),
    )
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "WAITING_EXTERNAL"
    assert flow.row(task_id)["attempts_used"] == 1
    invocation = flow.db.execute("SELECT * FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()
    assert invocation["state"] == "UNCERTAIN"
    assert ModelResult.model_validate_json(invocation["result_json"]).ok is True
    reservation = flow.db.execute("SELECT * FROM budget_reservations WHERE task_id = ?", (task_id,)).fetchone()
    assert reservation["state"] == "UNCERTAIN" and Decimal(reservation["amount"]) > 0
    assert not flow.receipts(task_id)
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM secretary_reports WHERE role = 'trader'").fetchone()[0] == 0
    flow.reopen()
    monkeypatch.setattr(flow.gateway.scripted, "complete", lambda _: pytest.fail("replayed unknown-usage response"))
    assert flow.run() == 0
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations WHERE task_id = ?", (task_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["invalid_choice", "owner_pause"])
def test_failed_result_recovers_its_health_observation_from_saved_snapshot(tmp_path, monkeypatch, failure):
    flow = _consumer_flow(tmp_path)
    task_id = flow.add_turn()
    if failure == "invalid_choice":
        flow.gateway.scripted.outputs["trader"] = {"action": "hold"}
    else:
        flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "Synthetic owner pause.")

    def crash_before_finish(*args, **kwargs):
        raise KeyboardInterrupt("Synthetic crash after failed role result before task finish.")

    monkeypatch.setattr(flow.office.scheduler, "finish", crash_before_finish)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    result = flow.db.execute("SELECT * FROM role_results WHERE task_id = ?", (task_id,)).fetchone()
    assert result["status"] == "FAILED"
    original = json.loads(result["document_json"])
    assert flow.row(task_id)["status"] in {"LEASED", "RUNNING"}
    assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 0
    expected_receipts = int(failure == "invalid_choice")
    assert len(flow.receipts(task_id)) == expected_receipts
    snapshots = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    flow.expire(task_id)
    flow.reopen()
    monkeypatch.setattr(
        flow.gateway.scripted, "complete", lambda _: pytest.fail("replayed terminal failed Trader call")
    )
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "FAILED"
    assert json.loads(flow.row(task_id)["output_json"]) == original
    assert len(flow.receipts(task_id)) == expected_receipts
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    count = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    assert count == snapshots
    observation = flow.db.execute("SELECT * FROM version_observations").fetchall()
    assert len(observation) == 1 and observation[0]["observation_id"] == task_id
    assert json.loads(observation[0]["document_json"])["ok"] is False
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    candidate = flow.db.execute(
        "SELECT state FROM candidates WHERE candidate_id = ?", (flow.candidate_id,)
    ).fetchone()
    assert candidate["state"] == "ROLLED_BACK"
    assert flow.run() == 0


def test_crash_after_health_sample_rolls_back_task_finish_and_recovers_one_sample(tmp_path, monkeypatch):
    flow = _consumer_flow(tmp_path, observation_policy=ObservationPolicy(min_decisions=1))
    task_id = flow.add_turn()
    observe = flow.runtime.observe

    def observe_then_crash(task, output):
        observe(task, output)
        assert flow.row(task_id)["status"] == "SUCCEEDED"
        assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 1
        raise KeyboardInterrupt("Synthetic crash after health sample before transaction commit.")

    monkeypatch.setattr(flow.runtime, "observe", observe_then_crash)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    decision = flow.db.execute("SELECT * FROM decisions").fetchone()
    assert decision is not None
    assert len(flow.receipts(task_id)) == 1
    assert flow.row(task_id)["status"] in {"LEASED", "RUNNING"}
    assert flow.row(task_id)["output_json"] is None
    assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'observation_passed'").fetchone()[0] == 0
    snapshots = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    flow.expire(task_id)
    flow.reopen()
    monkeypatch.setattr(flow.gateway.scripted, "complete", lambda _: pytest.fail("replayed health-sampled Trader call"))
    assert flow.run() == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED"
    assert json.loads(flow.row(task_id)["output_json"])["decision_id"] == decision["decision_id"]
    assert flow.row(task_id)["attempts_used"] == 1
    assert len(flow.receipts(task_id)) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    count = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    assert count == snapshots
    observation = flow.db.execute("SELECT * FROM version_observations").fetchall()
    assert len(observation) == 1 and observation[0]["observation_id"] == decision["decision_id"]
    assert json.loads(observation[0]["document_json"])["ok"] is True
    assert flow.db.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'observation_passed'").fetchone()[0] == 1
    candidate = flow.db.execute(
        "SELECT state FROM candidates WHERE candidate_id = ?", (flow.candidate_id,)
    ).fetchone()
    assert candidate["state"] == "ACTIVE"
    assert flow.run() == 0
    assert flow.db.execute("SELECT COUNT(*) FROM version_observations").fetchone()[0] == 1


@pytest.mark.parametrize("action", ["hold", "enter"])
def test_trader_effect_committed_before_worker_finish_recovers_once_after_reopen(tmp_path, monkeypatch, action):
    flow = _consumer_flow(tmp_path)
    flow.gateway.scripted.outputs["trader"] = _choice(action)
    task_id = flow.add_turn()

    def crash_before_finish(*args, **kwargs):
        raise KeyboardInterrupt("Synthetic crash after committed Trader result before task finish.")

    monkeypatch.setattr(flow.office.scheduler, "finish", crash_before_finish)
    with pytest.raises(KeyboardInterrupt):
        flow.run()
    original = flow.db.execute("SELECT * FROM decisions").fetchone()
    assert original is not None
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert len(flow.receipts(task_id)) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == int(action == "enter")
    snapshots = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    flow.expire(task_id)
    flow.reopen()
    monkeypatch.setattr(flow.gateway.scripted, "complete", lambda _: pytest.fail("replayed committed Trader call"))
    assert flow.run() == 1
    output = json.loads(flow.row(task_id)["output_json"])
    assert flow.row(task_id)["status"] == "SUCCEEDED"
    assert output["decision_id"] == original["decision_id"]
    assert flow.row(task_id)["attempts_used"] == 1
    assert len(flow.receipts(task_id)) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == int(action == "enter")
    count = flow.db.execute("SELECT COUNT(*) FROM snapshots WHERE portfolio_id = ?", (flow.pid,)).fetchone()[0]
    assert count == snapshots
    assert flow.db.execute("SELECT COUNT(*) FROM secretary_reports WHERE role = 'trader'").fetchone()[0] == 1
    assert flow.db.execute("SELECT COUNT(*) FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    assert flow.run() == 0
