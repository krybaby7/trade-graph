"""Owner-approved manifest changes preserve authenticated financial continuity."""
import asyncio
import copy
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_protected_financial_runtime import ENTER, HOLD, KEY, activate, request, stack

from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.financial_service import ProtectedFinancialService


def transition_stack(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    runtime.financial.dispatch(request(context))
    asyncio.run(runtime.financial.dispatch_outbox())
    quote = execution.latest_observation("BTC/USD", execution.now())
    execution.on_observation(quote.model_copy(update={"observation_id": "transition-fill"}))
    runtime.financial.retain_budget_history(portfolio)
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "authenticated synthetic upgrade")
    target = ProtectedFinancialService(db, clock, execution,
        manifest=replace(runtime.financial.manifest, wall_seconds=3), capability_key=KEY)
    return db, clock, ledger, execution, portfolio, runtime, target


def operator(financial):
    from trade_graph.kernel.financial_transition import FinancialManifestTransition
    return FinancialManifestTransition(financial)


def approval_for(target, old, *, operation_id="synthetic-upgrade"):
    return operator(target).inspect(source_manifests=[asdict(old.manifest)],
        operation_id=operation_id, expires_at=utc_iso(target.clock.now() + timedelta(minutes=10)))


def facts(db):
    tables = ("ledger_events", "journal_postings", "fills", "order_attempts", "budget_reservations",
              "usage_receipts", "cost_allocations", "protected_financial_budget_origins",
              "model_invocations", "provider_transport_attempts")
    return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables}


def test_owner_transition_preserves_fills_attempts_and_restart_without_granting_resume(tmp_path):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    before = facts(db)
    approval = approval_for(target, old.financial)
    outcome = operator(target).apply(approval)
    assert outcome["status"] == "APPLIED"
    assert facts(db) == before and len(before["fills"]) == len(before["order_attempts"]) == 1
    assert execution.profile(portfolio) == "MANAGE_ONLY"
    assert not old.financial.history.ready(portfolio) and target.history.ready(portfolio)
    restarted = ProtectedPaperRuntime(database=db, clock=clock, execution=execution, manifest=target.manifest,
        capability_key=KEY, instance_id="target-protected")
    restarted.controller.admit_release(release_id="target-release", source_text=HOLD)
    restarted.controller.activate_release("target-release")
    with pytest.raises(AuthorityDenied, match="pause"):
        restarted.financial.issue("target-protected", portfolio, "BTC/USD")
    assert operator(target).apply(approval) == outcome
    assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 1


@pytest.mark.parametrize("attack", ["target", "source_manifest", "witness", "database", "state",
                                    "expiry", "unpaused", "system_pause", "key", "ledger"])
def test_wrong_stale_unapproved_transition_refuses_without_any_writes(tmp_path, attack):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    if attack == "target":
        approval["target_manifest"]["wall_seconds"] = 4
    elif attack == "source_manifest":
        approval["source_manifests"][0]["deployment_id"] = "different-deployment"
    elif attack == "witness":
        approval["source_witness_sha256"] = "0" * 64
    elif attack == "database":
        approval["database_identity"][1] += 1
    elif attack == "state":
        ledger.deposit(portfolio, "USD", Decimal(1), "post-inspection-owner-flow")
    elif attack == "expiry":
        clock.advance(601)
    elif attack in {"unpaused", "system_pause"}:
        execution.set_pause(portfolio, "RUNNING", "owner", "replace owner latch")
        execution.set_pause(portfolio, "RUNNING" if attack == "unpaused" else "MANAGE_ONLY",
                            "owner" if attack == "unpaused" else "system", "changed")
    elif attack == "key":
        target = ProtectedFinancialService(db, clock, execution, manifest=target.manifest, capability_key=b"x" * 32)
    else:
        db.execute("UPDATE ledger_events SET payload_json='{}' WHERE sequence=1")
    checkpoints = db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0]
    witness = target.history.path.read_bytes()
    with pytest.raises((AuthorityDenied, StaleState, ValueError)):
        operator(target).apply(approval)
    assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0] == checkpoints
    assert target.history.path.read_bytes() == witness


def test_second_transition_keeps_source_chain_and_rejects_old_authority(tmp_path):
    db, clock, ledger, execution, portfolio, first, second = transition_stack(tmp_path)
    operator(second).apply(approval_for(second, first.financial))
    third = ProtectedFinancialService(db, clock, execution,
        manifest=replace(second.manifest, wall_seconds=4), capability_key=KEY)
    operator(third).apply(approval_for(third, second, operation_id="synthetic-upgrade-2"))
    assert third.history.ready(portfolio)
    assert not first.financial.history.ready(portfolio) and not second.history.ready(portfolio)
    witness = json.loads(third.history.path.read_text())["payload"]
    assert len(witness["scopes"]) == 3 and len(witness["transitions"]) == 2
    with pytest.raises((AuthorityDenied, StaleState)):
        operator(first.financial).inspect(source_manifests=[asdict(third.manifest)],
            operation_id="rollback", expires_at=utc_iso(clock.now() + timedelta(minutes=10)))


@pytest.mark.parametrize("stage", ["before_commit", "after_commit", "after_publish"])
def test_interruption_is_atomic_or_recovers_only_exact_committed_transition(tmp_path, monkeypatch, stage):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    saved_witness = target.history.path.read_bytes()
    if stage == "before_commit":
        db.execute("""CREATE TRIGGER reject_transition BEFORE INSERT ON protected_financial_transitions
            BEGIN SELECT RAISE(ABORT,'synthetic transition fault'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="transition fault"):
            operator(target).apply(approval)
        assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 0
        assert target.history.path.read_bytes() == saved_witness and old.financial.history.ready(portfolio)
        db.execute("DROP TRIGGER reject_transition")
    else:
        original = target.history._write_witness
        def interrupted(directory, payload):
            if stage == "after_publish":
                original(directory, payload)
            raise OSError("synthetic transition publication crash")
        monkeypatch.setattr(target.history, "_write_witness", interrupted)
        with pytest.raises(OSError, match="publication crash"):
            operator(target).apply(approval)
        assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 1
        assert not old.financial.history.ready(portfolio)
        if stage == "after_commit":
            assert not target.history.ready(portfolio)
        monkeypatch.setattr(target.history, "_write_witness", original)
    before = facts(db)
    assert operator(target).apply(approval)["status"] == "APPLIED"
    assert target.history.ready(portfolio) and facts(db) == before
    assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 1


def test_pending_transition_recovery_refuses_changed_facts_or_approval(tmp_path, monkeypatch):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    original = target.history._write_witness
    def interrupted(*args):
        raise OSError("synthetic crash")
    monkeypatch.setattr(target.history, "_write_witness", interrupted)
    with pytest.raises(OSError):
        operator(target).apply(approval)
    monkeypatch.setattr(target.history, "_write_witness", original)
    changed = copy.deepcopy(approval)
    changed["expires_at"] = utc_iso(clock.now() + timedelta(minutes=20))
    with pytest.raises((AuthorityDenied, StaleState)):
        operator(target).apply(changed)
    ledger.deposit(portfolio, "USD", Decimal(1), "post-commit-drift")
    witness = target.history.path.read_bytes()
    with pytest.raises(StaleState, match="changed"):
        operator(target).apply(approval)
    assert target.history.path.read_bytes() == witness and not target.history.ready(portfolio)


def test_transition_requires_exclusive_database_and_immutable_receipt(tmp_path):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    with old._exclusive_controller():
        with pytest.raises(StaleState, match="owns"):
            operator(target).apply(approval)
    operator(target).apply(approval)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM protected_financial_transitions")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE protected_financial_transitions SET authentication='bad'")

def test_transition_preserves_original_paid_receipts_and_uncertain_holds(tmp_path):
    from tests.integration.test_protected_budget_continuity import reserve
    from tests.integration.test_protected_department_graph import GRAPH, protected_flow
    flow, old = protected_flow(tmp_path)
    flow.add("research")
    assert flow.run("research") == 1
    reservation = reserve(flow, synthetic=False)
    flow.assembly.gateway.budget.mark_uncertain(reservation)
    old.financial.retain_budget_history(flow.pid)
    flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "synthetic upgrade")
    target = ProtectedFinancialService(flow.db, flow.clock, flow.office.execution,
        manifest=replace(old.financial.manifest, wall_seconds=3),
        capability_key=old.financial._capability_key)
    before, allowance = facts(flow.db), flow.office.budget.remaining("deployment")
    operator(target).apply(approval_for(target, old.financial))
    assert facts(flow.db) == before and flow.office.budget.remaining("deployment") == allowance
    target.origins.verify_all()
    restarted = ProtectedPaperRuntime(database=flow.db, clock=flow.clock, execution=flow.office.execution,
        manifest=target.manifest, capability_key=target._capability_key, instance_id="upgraded-budget")
    restarted.controller.admit_release(release_id="upgraded-budget-release", source_text=GRAPH)
    assert facts(flow.db) == before
    target.origins.verify_all()
    assert flow.db.execute("SELECT state FROM budget_reservations WHERE reservation_id=?",
                           (reservation,)).fetchone()[0] == "UNCERTAIN"


def test_subscription_evidence_and_unknown_cost_survive_transition_and_identity_cannot_change(tmp_path):
    from tests.unit.test_subscription_adapter import request as model_request

    from trade_graph.adapters.models.subscription import SubscriptionJournal
    from trade_graph.contracts.models import ModelResult
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    journal = SubscriptionJournal(db, clock)
    model = model_request().model_copy(update={"provider": "codex_subscription", "model": "gpt-6.1-sol"})
    journal.begin("retained-subscription", model, "codex_subscription", {"remaining_percent": 80})
    attempt = journal.begin_attempt("retained-subscription", 1, model, "codex_subscription",
                                    quota={"remaining_percent": 80})
    result = ModelResult(ok=False, failure="timeout_uncertain", message="synthetic retained unknown cost")
    journal.save_attempt(attempt, result, "UNCERTAIN")
    journal.save("retained-subscription", result, "UNCERTAIN")
    before = {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
              for table in ("subscription_invocations", "subscription_attempts")}
    operator(target).apply(approval_for(target, old.financial))
    assert {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in before} == before
    with target.history.witness_lock() as directory:
        checkpoint, _ = target.history.previous(portfolio, directory)
    assert checkpoint["tables"]["subscription_invocations"]["rows"] == 1
    assert checkpoint["tables"]["subscription_attempts"]["rows"] == 1
    assert before["subscription_invocations"][0]["cost_status"] == "unknown"
    assert before["subscription_attempts"][0]["actual_cost_native"] is None
    db.execute("UPDATE subscription_attempts SET request_hash='different-request'")
    with pytest.raises(StaleState, match="original financial effect identity"):
        target.retain_budget_history(portfolio)


def test_transition_database_rollback_cannot_drop_upgrade_or_restore_old_authority(tmp_path):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    backup = sqlite3.connect(tmp_path / "before-transition.sqlite")
    db.connection.backup(backup)
    operator(target).apply(approval)
    backup.backup(db.connection)
    backup.close()
    assert not target.history.ready(portfolio) and not old.financial.history.ready(portfolio)
    with pytest.raises(StaleState):
        operator(target).apply(approval)


def test_transition_size_refusal_does_not_fetch_oversized_payload(tmp_path, monkeypatch):
    from trade_graph.kernel import financial_transition
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    operator(target).apply(approval_for(target, old.financial))
    monkeypatch.setattr(financial_transition, "MAXIMUM_TRANSITION_BYTES", 1)
    queries = []
    db.connection.set_trace_callback(queries.append)
    try:
        assert not target.history.ready(portfolio)
    finally:
        db.connection.set_trace_callback(None)
    assert not any("SELECT *" in query.upper() and "protected_financial_transitions" in query
                   for query in queries)

def test_transition_refuses_scope_bound_before_commit(tmp_path, monkeypatch):
    from trade_graph.kernel import financial_transition
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    approval = approval_for(target, old.financial)
    monkeypatch.setattr(financial_transition, "MAXIMUM_WITNESS_SCOPES", 1)
    with pytest.raises(StaleState, match="bound"):
        operator(target).apply(approval)
    assert db.execute("SELECT count(*) FROM protected_financial_transitions").fetchone()[0] == 0

def test_old_executable_pin_is_historical_data_and_target_pin_must_be_current(tmp_path, monkeypatch):
    from trade_graph.kernel import runtime_manifest
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    monkeypatch.setattr(runtime_manifest, "protected_package_sha256", lambda: "a" * 64)
    target = ProtectedFinancialService(db, clock, execution,
        manifest=replace(target.manifest, protected_package_sha256="a" * 64), capability_key=KEY)
    approval = approval_for(target, old.financial)
    assert operator(target).apply(approval)["status"] == "APPLIED"
    assert target.history.ready(portfolio)
    monkeypatch.setattr(runtime_manifest, "protected_package_sha256", lambda: "b" * 64)
    with pytest.raises(PermissionError, match="pin"):
        operator(target).apply(approval)


def test_transition_receipt_tampering_or_omission_refuses_all_financial_authority(tmp_path):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    operator(target).apply(approval_for(target, old.financial))
    db.execute("DROP TRIGGER protected_financial_transitions_no_update")
    db.execute("UPDATE protected_financial_transitions SET authentication=?", ("0" * 64,))
    assert not target.history.ready(portfolio) and not old.financial.history.ready(portfolio)

def test_operator_cli_reads_only_protected_approval_and_locks_before_open_until_close(tmp_path, monkeypatch, capsys):
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.domain import clock as clocks
    from trade_graph.kernel import deployment_image, financial_transition
    from trade_graph.kernel.runtime_manifest import canonical_json
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    owner, source = tmp_path / "target-owner", tmp_path / "source-owner"
    documents = {(owner, "runtime-manifest.json"): canonical_json(asdict(target.manifest)).encode(),
                 (source, "runtime-manifest.json"): canonical_json(asdict(old.financial.manifest)).encode(),
                 (owner, "capability.key"): KEY}
    def protected_read(directory, name, maximum_bytes):
        return documents[(directory, name)]
    monkeypatch.setattr(deployment_image, "read_owner_file", protected_read)
    monkeypatch.setattr(clocks, "SystemClock", lambda: clock)
    original_open, original_close = Database.__init__, Database.close
    boundaries = []
    def locked_boundary(self, path):
        with pytest.raises(StaleState, match="owns"):
            with old._exclusive_controller():
                pass
        boundaries.append("before-open")
        original_open(self, path)
    def locked_close(self):
        with pytest.raises(StaleState, match="owns"):
            with old._exclusive_controller():
                pass
        boundaries.append("before-close")
        original_close(self)
    monkeypatch.setattr(Database, "__init__", locked_boundary)
    monkeypatch.setattr(Database, "close", locked_close)
    common = ["--database", str(db.path), "--protected-owner", str(owner)]
    assert financial_transition.main(["inspect", *common, "--source-manifest",
        str(source / "runtime-manifest.json"), "--operation-id", "cli-upgrade", "--expires-at",
        utc_iso(clock.now() + timedelta(minutes=10))]) == 0
    proposal = json.loads(capsys.readouterr().out)
    documents[(owner, "financial-continuity-approval.json")] = canonical_json(proposal).encode()
    assert financial_transition.main(["apply", *common, "--operation-id", "override"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    assert financial_transition.main(["apply", *common]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "APPLIED"
    assert boundaries == ["before-open", "before-close"] * 3
    assert target.history.ready(portfolio)

def test_transition_revokes_outstanding_old_capabilities_without_replaying_effects(tmp_path):
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    execution.set_pause(portfolio, "RUNNING", "owner", "synthetic pre-upgrade proposal")
    context = old.financial.issue("protected-test", portfolio, "BTC/USD")
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "synthetic transition")
    before = facts(db)
    operator(target).apply(approval_for(target, old.financial))
    assert db.execute("SELECT state FROM protected_rpc_requests WHERE request_id=?",
                      (context["request_id"],)).fetchone()[0] == "REVOKED"
    for service in (old.financial, target):
        with pytest.raises(StaleState, match="revoked"):
            service.dispatch(request(context, action="hold"))
    assert facts(db) == before


def test_transition_requires_all_existing_portfolios_and_retains_all_scopes(tmp_path):
    from trade_graph.application.authority import seed_paper_authority
    db, clock, ledger, execution, portfolio, old, target = transition_stack(tmp_path)
    other = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(other, "USD", Decimal("100"), "other-virtual-capital")
    seed_paper_authority(db, clock, other)
    old.financial.prepare_history(other, instance_id=old.controller.instance_id)
    with pytest.raises(AuthorityDenied, match="every portfolio"):
        approval_for(target, old.financial)
    execution.set_pause(other, "MANAGE_ONLY", "owner", "synthetic global upgrade")
    approval = approval_for(target, old.financial)
    before = facts(db)
    outcome = operator(target).apply(approval)
    assert set(outcome["portfolio_ids"]) == {portfolio, other}
    assert target.history.ready(portfolio) and target.history.ready(other)
    assert facts(db) == before
