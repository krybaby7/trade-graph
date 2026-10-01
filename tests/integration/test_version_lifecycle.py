"""Real artifact bytes, durable restart and financial state survive rollout recovery."""

import asyncio
import hashlib
import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_engineer import _task
from tests.integration.test_execution import _decision, _quote, _rules
from tests.leadership_support import commission

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import ObservationPolicy, VersionController
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import StaleState, ValidationFailure

POLICY = {
    "schema_version": 1,
    "max_general_lessons": 8,
    "always_include": ["mandate_obligations", "active_safety"],
}


def lifecycle_stack(tmp_path, *, observation_policy=None, additional_files=None, allowed_paths=None):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "lifecycle.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    source = tmp_path / "source"
    files = {"artifacts/context_policy.json": json.dumps(POLICY), **(additional_files or {})}
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    engineer = ArtifactEngineer(database, clock, source, ledger)
    versions = VersionController(database, clock, observation_policy=observation_policy or ObservationPolicy())
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(portfolio, "v1", baseline)
    task = _task(clock, portfolio, baseline, allowed_paths=allowed_paths or list(files))
    commission(engineer, portfolio, task)
    return SimpleNamespace(
        clock=clock, database=database, ledger=ledger, portfolio=portfolio, source=source,
        engineer=engineer, versions=versions, baseline=baseline, task=task, files=files,
    )


def candidate(stack, tmp_path, *, files=None):
    changed = files or {"artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 5})}
    result = stack.engineer.implement(stack.portfolio, stack.task.record_id, changed, tmp_path / "candidate")
    assert result.state == "READY", result.known_limits
    return result


def activate(stack, result):
    stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})
    return stack.versions.begin(stack.portfolio, "fixture", reconcile=lambda: None)


def _rows(database, table):
    return [tuple(row) for row in database.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]


def test_activation_consumes_persisted_tested_bytes_after_worktrees_disappear(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    shutil.rmtree(stack.source)
    shutil.rmtree(tmp_path / "candidate")
    bundle = activate(stack, result)
    content = bundle["files"]["artifacts/context_policy.json"]
    assert json.loads(content)["max_general_lessons"] == 5
    assert bundle["artifact_hash"] == result.content_hash
    entry = next(x for x in bundle["manifest"]["files"] if x["path"] == "artifacts/context_policy.json")
    assert entry["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    stack.database.close()
    reopened = Database(tmp_path / "lifecycle.sqlite")
    restored = VersionController(reopened, stack.clock)
    reloaded = restored.begin(stack.portfolio, "after-restart", reconcile=lambda: None)
    assert reloaded == bundle
    reopened.close()


def test_generation_rejects_old_bundle_after_rollback_to_identical_baseline(tmp_path):
    stack = lifecycle_stack(tmp_path)
    old = stack.versions.begin(stack.portfolio, "fixture", reconcile=lambda: None)
    result = candidate(stack, tmp_path)
    new = activate(stack, result)
    with pytest.raises(StaleState):
        stack.versions.assert_current(stack.portfolio, old)
    stack.versions.rollback(stack.portfolio, stack.baseline, "v1")
    restored = stack.versions.begin(stack.portfolio, "fixture", reconcile=lambda: None)
    assert restored["artifact_hash"] == old["artifact_hash"]
    assert restored["generation"] > new["generation"] > old["generation"]
    with pytest.raises(StaleState):
        stack.versions.assert_current(stack.portfolio, old)
    with pytest.raises(StaleState):
        stack.versions.assert_current(stack.portfolio, new)
    stack.versions.assert_current(stack.portfolio, restored)


@pytest.mark.parametrize("mutation", ["bytes", "manifest", "checker_input"])
def test_activation_rejects_candidate_evidence_tampering_and_retains_receipt(tmp_path, mutation):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    row = stack.database.execute(
        "SELECT report_json FROM candidate_attestations WHERE attestation_id = ?", (result.attestation_id,),
    ).fetchone()
    report = json.loads(row[0])
    if mutation == "bytes":
        report["artifact_files"]["artifacts/context_policy.json"] = json.dumps({**POLICY, "max_general_lessons": 4})
    elif mutation == "manifest":
        report["manifest"]["files"][0]["sha256"] = "0" * 64
    else:
        checked = json.loads(report["stdout"])
        checked["input_sha256"] = "0" * 64
        report["stdout"] = json.dumps(checked)
    stack.database.execute(
        "UPDATE candidate_attestations SET report_json = ? WHERE attestation_id = ?",
        (json.dumps(report), result.attestation_id),
    )
    with pytest.raises(ValidationFailure):
        stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})
    assert stack.versions.current_hash(stack.portfolio) == stack.baseline
    assert stack.database.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'activate'").fetchone()[0] == 0
    assert stack.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1


def test_activation_crash_before_event_commit_is_atomic_and_retryable(tmp_path, monkeypatch):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    before = _rows(stack.database, "active_versions")
    record = stack.versions._event

    def crash(*_args, **_kwargs):
        raise RuntimeError("process died before rollout journal commit")

    monkeypatch.setattr(stack.versions, "_event", crash)
    with pytest.raises(RuntimeError, match="process died"):
        stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})
    assert _rows(stack.database, "active_versions") == before
    assert stack.database.execute("SELECT state FROM candidates").fetchone()[0] == "READY"
    assert stack.database.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'activate'").fetchone()[0] == 0
    monkeypatch.setattr(stack.versions, "_event", record)
    activate(stack, result)
    assert stack.database.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'activate'").fetchone()[0] == 1


def test_reconciliation_failure_does_not_acknowledge_reload_and_automatically_rolls_back(tmp_path):
    stack = lifecycle_stack(tmp_path)
    stack.versions.begin(stack.portfolio, "healthy-baseline", reconcile=lambda: None)
    result = candidate(stack, tmp_path)
    stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})

    def unavailable_reconciliation():
        raise RuntimeError("broker reconciliation unavailable")

    with pytest.raises(RuntimeError, match="reconciliation unavailable"):
        stack.versions.begin(stack.portfolio, "bad-reload", reconcile=unavailable_reconciliation)
    acknowledgements = stack.database.execute(
        "SELECT COUNT(*) FROM consumer_loads WHERE consumer_id = 'bad-reload'",
    ).fetchone()[0]
    assert acknowledgements == 0
    assert stack.versions.maintain(stack.portfolio) == "ROLLED_BACK"
    assert stack.versions.current_hash(stack.portfolio) == stack.baseline
    assert stack.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1


def test_corrupt_active_bytes_trigger_rollback_before_a_consumer_can_acknowledge(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})
    corrupted = {"artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 4})}
    stack.database.execute(
        "UPDATE artifact_bundles SET files_json = ? WHERE artifact_hash = ?",
        (json.dumps(corrupted), result.content_hash),
    )
    with pytest.raises(ValidationFailure, match="identity"):
        stack.versions.begin(stack.portfolio, "corrupted-reload", reconcile=lambda: None)
    assert stack.database.execute("SELECT COUNT(*) FROM consumer_loads").fetchone()[0] == 0
    assert stack.versions.maintain(stack.portfolio) == "ROLLED_BACK"
    assert stack.versions.current_hash(stack.portfolio) == stack.baseline


@pytest.mark.parametrize("corruption", ["missing", "changed_bytes"])
def test_corrupt_previous_bytes_block_recovery_without_fabricating_a_rollback(tmp_path, corruption):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    bundle = activate(stack, result)
    if corruption == "missing":
        stack.database.execute("DELETE FROM artifact_bundles WHERE artifact_hash = ?", (stack.baseline,))
    else:
        stack.database.execute(
            "UPDATE artifact_bundles SET files_json = '{}' WHERE artifact_hash = ?", (stack.baseline,),
        )
    stack.versions.observe(stack.portfolio, bundle, "failed-load", ok=False, reason="health failure")
    assert stack.versions.maintain(stack.portfolio) == "BLOCKED"
    assert stack.versions.current_hash(stack.portfolio) == result.content_hash
    assert stack.database.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'rollback'").fetchone()[0] == 0
    with pytest.raises(ValidationFailure, match="pending"):
        stack.versions.begin(stack.portfolio, "next-turn", reconcile=lambda: None)


def test_unacknowledged_generation_rolls_back_at_reload_deadline(tmp_path):
    stack = lifecycle_stack(tmp_path, observation_policy=ObservationPolicy(reload_timeout_seconds=10))
    result = candidate(stack, tmp_path)
    stack.versions.activate(stack.portfolio, {"candidate_id": result.candidate_id})
    stack.clock.advance(9)
    assert stack.versions.maintain(stack.portfolio) == "RESTART_PENDING"
    assert stack.versions.current_hash(stack.portfolio) == result.content_hash
    stack.clock.advance(1)
    assert stack.versions.maintain(stack.portfolio) == "ROLLED_BACK"
    assert stack.versions.current_hash(stack.portfolio) == stack.baseline


def test_expired_worker_lease_does_not_falsely_mark_healthy_artifact_as_failed(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    activate(stack, result)
    scheduler = Scheduler(stack.database, stack.clock)
    scheduler.add_task(role="trader", objective="expires before reload", portfolio_id=stack.portfolio)
    lease = scheduler.claim("expired-worker", roles={"trader"})
    assert lease is not None
    stack.clock.advance(31)
    with pytest.raises(StaleState, match="lease"):
        stack.versions.begin(stack.portfolio, "expired-worker", reconcile=lambda: None, lease=lease)
    assert stack.database.execute("SELECT state FROM version_rollouts").fetchone()[0] == "OBSERVING"
    assert stack.versions.current_hash(stack.portfolio) == result.content_hash


def test_failed_health_rolls_back_after_restart_and_preserves_unknown_partial_order(tmp_path):
    stack = lifecycle_stack(tmp_path)
    ledger, database, clock, portfolio = stack.ledger, stack.database, stack.clock, stack.portfolio
    ledger.deposit(portfolio, "USD", Decimal("10000"), "capital")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    inner = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, DropAckBroker(inner))
    execution.register_instrument(_rules())
    execution.save_observation(_quote(clock, "99", "100", observation_id="initial"))
    opening = _decision(clock, portfolio, system_version_id=stack.baseline, record_id="opening")
    intent = execution.authorize(portfolio, opening)
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    assert inner.submit_count == 1
    # A real fill occurs at the broker while its local acknowledgement remains uncertain.
    clock.advance(1)
    fills = inner.match(_quote(clock, "99", "100", size="0.004", observation_id="partial"))
    assert len(fills) == 1
    result = candidate(stack, tmp_path)
    bundle = activate(stack, result)
    assert execution.intent_state(intent) == "UNKNOWN"
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent) == "PARTIALLY_FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.004")
    execution.set_pause(portfolio, "NO_NEW_EXPOSURE", "owner", "owner pause during rollout")
    ledger.add_expense(
        portfolio, expense_id="paid-model", native_amount=Decimal("0.02"), native_currency="USD",
        reporting_amount=Decimal("0.02"), reporting_currency="USD", embedded=False, source="synthetic-fixture",
    )
    durable_tables = (
        "order_intents", "fills", "ledger_events", "journal_transactions", "journal_postings",
        "usage_receipts", "decisions", "broker_orders", "position_reservations", "pause_states",
        "owner_policy_revisions", "mandates", "deployment_budget", "cost_allocations", "budget_reservations",
    )
    before = {table: _rows(database, table) for table in durable_tables}
    stack.versions.observe(portfolio, bundle, "load-failure", ok=False, reason="required context missing")
    database.close()
    reopened = Database(tmp_path / "lifecycle.sqlite")
    restored = VersionController(reopened, clock)
    restored.maintain(portfolio)
    assert restored.current_hash(portfolio) == stack.baseline
    for table, rows in before.items():
        assert _rows(reopened, table) == rows, table
    restarted_ledger = Ledger(reopened, clock)
    restarted_broker = PaperBroker(reopened, clock)
    recovered = Execution(reopened, restarted_ledger, clock, restarted_broker)
    asyncio.run(recovered.reconcile())
    asyncio.run(recovered.dispatch())
    assert restarted_broker.submit_count == 0
    clock.advance(1)
    recovered.on_observation(_quote(clock, "99", "100", observation_id="remainder"))
    assert recovered.intent_state(intent) == "FILLED"
    assert recovered.owned_quantity(portfolio, "BTC") == Decimal("0.01")
    opening_row = reopened.execute("SELECT system_version_id FROM decisions WHERE decision_id = 'opening'").fetchone()
    assert opening_row[0] == stack.baseline
    assert restarted_ledger.journal_balanced(portfolio)
    assert reopened.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'rollback'").fetchone()[0] == 1
    reopened.close()


def test_observation_timeout_uses_recorded_policy_after_controller_restart(tmp_path):
    policy = ObservationPolicy(min_decisions=3, max_failures=0, horizon_seconds=30)
    stack = lifecycle_stack(tmp_path, observation_policy=policy)
    result = candidate(stack, tmp_path)
    activate(stack, result)
    stack.database.close()
    stack.clock.advance(31)
    reopened = Database(tmp_path / "lifecycle.sqlite")
    # A new process cannot extend the horizon by changing constructor defaults.
    restored = VersionController(reopened, stack.clock, observation_policy=ObservationPolicy(horizon_seconds=3600))
    restored.maintain(stack.portfolio)
    assert restored.current_hash(stack.portfolio) == stack.baseline
    assert reopened.execute("SELECT state FROM candidates").fetchone()[0] == "ROLLED_BACK"
    assert reopened.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    reopened.close()


def test_observation_cannot_claim_success_without_a_retained_decision_snapshot(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    bundle = activate(stack, result)
    with pytest.raises(ValidationFailure):
        stack.versions.observe(stack.portfolio, bundle, "invented-success", ok=True, required_retained=True)


def test_automatic_rollback_waits_for_leased_decision_boundary_and_blocks_next_turn(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    bundle = activate(stack, result)
    scheduler = Scheduler(stack.database, stack.clock)
    scheduler.add_task(
        role="trader", objective="already leased old-version turn", portfolio_id=stack.portfolio,
        expected_version=result.content_hash,
    )
    lease = scheduler.claim("running-trader", roles={"trader"})
    assert lease is not None
    stack.versions.observe(stack.portfolio, bundle, "failed-quality", ok=False, reason="quality regression")
    stack.versions.maintain(stack.portfolio)
    assert stack.versions.current_hash(stack.portfolio) == result.content_hash
    with pytest.raises((StaleState, ValidationFailure)):
        stack.versions.begin(stack.portfolio, "next-turn", reconcile=lambda: None)
    scheduler.finish(lease, {"no_effect": "interrupted at rollout boundary"}, "FAILED")
    stack.versions.maintain(stack.portfolio)
    assert stack.versions.current_hash(stack.portfolio) == stack.baseline
    # The deterministic controller is idempotent after its automatic rollback.
    stack.versions.maintain(stack.portfolio)
    assert stack.database.execute("SELECT COUNT(*) FROM version_events WHERE kind = 'rollback'").fetchone()[0] == 1
