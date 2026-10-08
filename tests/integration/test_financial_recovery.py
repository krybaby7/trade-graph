"""An offline approved inode transition retains the original financial witness."""
import json
import shutil
from dataclasses import asdict
from datetime import timedelta

import pytest
from tests.integration.test_financial_transition import facts, transition_stack
from tests.integration.test_protected_financial_runtime import KEY

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.financial_service import ProtectedFinancialService


def recovery_stack(tmp_path, *, subscriptions=False):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    db, clock, ledger, execution, pid, old, target = transition_stack(original)
    if subscriptions:
        from tests.unit.test_subscription_adapter import request as model_request

        from trade_graph.adapters.models.subscription import SubscriptionJournal
        from trade_graph.contracts.models import ModelResult
        journal = SubscriptionJournal(db, clock)
        model = model_request().model_copy(update={"provider": "codex_subscription", "model": "gpt-6.1-sol"})
        for index in range(13):
            invocation = f"retained-invocation-{index}"
            model = model.model_copy(update={"task_id": f"retained-task-{index}"})
            journal.begin(invocation, model, "codex_subscription", {"remaining_percent": 80})
            for attempt_index in range(2 if index < 2 else 1):
                attempt = journal.begin_attempt(invocation, attempt_index + 1, model, "codex_subscription",
                    quota={"remaining_percent": 80})
                result = ModelResult(ok=False, failure="timeout_uncertain", message="synthetic failed unknown cost")
                journal.save_attempt(attempt, result, "UNCERTAIN")
            journal.save(invocation, result, "UNCERTAIN")
        old.financial.retain_budget_history(pid)
    original_witness = old.financial.history.path.read_bytes()
    original_identity = db.file_identity()
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    destination = candidate / "protected.sqlite"
    import sqlite3
    with sqlite3.connect(destination) as handle:
        db.connection.backup(handle)
    destination.chmod(0o600)
    shutil.copyfile(old.financial.history.path, destination.with_suffix(".protected-financial-witness.json"))
    destination.with_suffix(".protected-financial-witness.json").chmod(0o600)
    recovered = Database(destination)
    recovered_execution = Execution(recovered, Ledger(recovered, clock), clock, PaperBroker(recovered, clock))
    financial = ProtectedFinancialService(recovered, clock, recovered_execution,
        manifest=old.financial.manifest, capability_key=KEY)
    return db, clock, pid, old, financial, original_identity, original_witness


def operator(financial):
    from trade_graph.kernel.financial_recovery import FinancialDatabaseRecovery
    return FinancialDatabaseRecovery(financial)


def proposal(financial, source_identity, *, operation_id="synthetic-recovery"):
    return operator(financial).inspect(source_database_identity=source_identity,
        source_manifests=[asdict(financial.manifest)],
        operation_id=operation_id, incident_evidence_sha256="a" * 64,
        expires_at=utc_iso(financial.clock.now() + timedelta(minutes=10)))


def test_explicit_recovery_preserves_original_facts_witness_and_pause(tmp_path):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    assert not recovered.history.ready(pid)
    approval = proposal(recovered, identity)
    before = facts(recovered.database)
    result = operator(recovered).apply(approval)
    assert result["status"] == "APPLIED"
    assert recovered.history.ready(pid)
    assert facts(recovered.database) == before
    assert old.financial.history.path.read_bytes() == original_witness
    assert original.file_identity() == identity
    assert recovered.execution.profile(pid) == "MANAGE_ONLY"
    source = json.loads(original_witness)["payload"]
    destination = json.loads(recovered.history.path.read_bytes())["payload"]
    assert destination["scopes"] == source["scopes"]
    assert destination["database_identity"] == recovered.database.file_identity() != identity
    assert destination["schema_version"] == 3 and len(destination["recoveries"]) == 1
    assert not result["paid_authorization"] and not result["live_authorization"]
    assert operator(recovered).apply(approval) == result


@pytest.mark.parametrize("attack", ["identity", "source", "state", "key", "expiry", "pause", "missing_checkpoint"])
def test_invalid_recovery_refuses_without_writing(tmp_path, attack):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    approval = proposal(recovered, identity)
    if attack == "identity":
        approval["candidate_database_identity"][1] += 1
    elif attack == "source":
        approval["source_witness_sha256"] = "0" * 64
    elif attack == "state":
        recovered.database.execute("UPDATE portfolios SET status='drift' WHERE portfolio_id=?", (pid,))
    elif attack == "key":
        recovered = ProtectedFinancialService(recovered.database, clock, recovered.execution,
            manifest=recovered.manifest, capability_key=b"x" * 32)
    elif attack == "expiry":
        clock.advance(601)
    elif attack == "pause":
        recovered.execution.set_pause(pid, "RUNNING", "owner", "synthetic drift")
    else:
        recovered.database.execute("DROP TRIGGER protected_financial_checkpoints_no_delete")
        recovered.database.execute("DELETE FROM protected_financial_checkpoints")
    saved = recovered.history.path.read_bytes()
    with pytest.raises((AuthorityDenied, StaleState, ValueError)):
        operator(recovered).apply(approval)
    assert recovered.history.path.read_bytes() == saved
    assert recovered.database.execute("SELECT count(*) FROM protected_financial_recoveries").fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["before_commit", "after_commit", "after_publish"])
def test_interrupted_recovery_finishes_only_same_committed_snapshot(tmp_path, monkeypatch, stage):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    approval = proposal(recovered, identity)
    if stage == "before_commit":
        recovered.database.execute("""CREATE TRIGGER reject_recovery BEFORE INSERT ON protected_financial_recoveries
            BEGIN SELECT RAISE(ABORT,'synthetic fault'); END""")
        approval = proposal(recovered, identity)
        with pytest.raises(Exception, match="synthetic fault"):
            operator(recovered).apply(approval)
        assert recovered.database.execute("SELECT count(*) FROM protected_financial_recoveries").fetchone()[0] == 0
        recovered.database.execute("DROP TRIGGER reject_recovery")
        approval = proposal(recovered, identity)
    else:
        original_write = recovered.history._write_witness
        def interrupted(directory, payload):
            if stage == "after_publish":
                original_write(directory, payload)
            raise OSError("synthetic publication crash")
        monkeypatch.setattr(recovered.history, "_write_witness", interrupted)
        with pytest.raises(OSError, match="publication crash"):
            operator(recovered).apply(approval)
        monkeypatch.setattr(recovered.history, "_write_witness", original_write)
        clock.advance(601)
    assert operator(recovered).apply(approval)["status"] == "APPLIED"
    assert recovered.history.ready(pid)
    assert recovered.database.execute("SELECT count(*) FROM protected_financial_recoveries").fetchone()[0] == 1


def test_committed_unpublished_recovery_refuses_changed_snapshot(tmp_path, monkeypatch):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    approval = proposal(recovered, identity)
    original_write = recovered.history._write_witness
    monkeypatch.setattr(recovered.history, "_write_witness", lambda *args: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        operator(recovered).apply(approval)
    monkeypatch.setattr(recovered.history, "_write_witness", original_write)
    recovered.database.execute("UPDATE portfolios SET status='drift' WHERE portfolio_id=?", (pid,))
    with pytest.raises(StaleState, match="changed"):
        operator(recovered).apply(approval)
    assert not recovered.history.ready(pid)


def test_recovery_requires_exclusive_ownership_and_immutable_receipt(tmp_path):
    import sqlite3
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    approval = proposal(recovered, identity)
    with recovered.database.exclusive_lock("another-worker"):
        with pytest.raises(StaleState, match="owns"):
            operator(recovered).apply(approval)
    operator(recovered).apply(approval)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        recovered.database.execute("DELETE FROM protected_financial_recoveries")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        recovered.database.execute("UPDATE protected_financial_recoveries SET authentication='bad'")


def test_recovery_then_manifest_transition_retains_recovery_chain(tmp_path):
    from dataclasses import asdict, replace

    from trade_graph.kernel.financial_transition import FinancialManifestTransition
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    operator(recovered).apply(proposal(recovered, identity))
    saved = json.loads(recovered.history.path.read_bytes())["payload"]
    target = ProtectedFinancialService(recovered.database, clock, recovered.execution,
        manifest=replace(recovered.manifest, wall_seconds=4), capability_key=KEY)
    upgrade = FinancialManifestTransition(target)
    approval = upgrade.inspect(source_manifests=[asdict(recovered.manifest)], operation_id="after-recovery",
        expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
    upgrade.apply(approval)
    result = json.loads(target.history.path.read_bytes())["payload"]
    assert result["schema_version"] == 3 and result["recoveries"] == saved["recoveries"]
    assert len(result["transitions"]) == 1 and target.history.ready(pid)
    assert not recovered.history.ready(pid)


def test_missing_recovery_receipt_or_broken_identity_chain_refuses(tmp_path):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    operator(recovered).apply(proposal(recovered, identity))
    recovered.database.execute("DROP TRIGGER protected_financial_recoveries_no_delete")
    recovered.database.execute("DELETE FROM protected_financial_recoveries")
    assert not recovered.history.ready(pid)


def test_completed_recovery_cannot_be_retried_with_changed_approval(tmp_path):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    approval = proposal(recovered, identity)
    operator(recovered).apply(approval)
    approval["incident_evidence_sha256"] = "b" * 64
    with pytest.raises(AuthorityDenied, match="identical"):
        operator(recovered).apply(approval)


def test_exact_thirteen_invocations_fifteen_failed_attempts_and_unknown_costs_survive(tmp_path):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path, subscriptions=True)
    tables = ("subscription_invocations", "subscription_attempts")
    before = {table: [dict(row) for row in recovered.database.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables}
    assert len(before["subscription_invocations"]) == 13 and len(before["subscription_attempts"]) == 15
    assert all(row["actual_cost_native"] is None for rows in before.values() for row in rows)
    operator(recovered).apply(proposal(recovered, identity))
    assert {table: [dict(row) for row in recovered.database.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables} == before
    assert recovered.history.ready(pid)
    recovered.database.execute("UPDATE subscription_attempts SET request_hash='changed' WHERE rowid=1")
    assert not recovered.history.ready(pid)
    with pytest.raises(StaleState, match="(identity|terminal subscription)"):
        recovered.retain_budget_history(pid)


@pytest.mark.parametrize("interrupted", [False, True])
def test_recovery_transition_second_recovery_and_pending_publication_lineage(tmp_path, monkeypatch, interrupted):
    import sqlite3
    from dataclasses import asdict, replace

    from trade_graph.kernel.financial_transition import FinancialManifestTransition
    original, clock, pid, old, first, identity, original_witness = recovery_stack(tmp_path)
    operator(first).apply(proposal(first, identity))
    target = ProtectedFinancialService(first.database, clock, first.execution,
        manifest=replace(first.manifest, wall_seconds=4), capability_key=KEY)
    upgrade = FinancialManifestTransition(target)
    approved = upgrade.inspect(source_manifests=[asdict(first.manifest)], operation_id="interleaved-upgrade",
        expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
    upgrade.apply(approved)
    destination = tmp_path / "second"
    destination.mkdir(mode=0o700)
    path = destination / "protected.sqlite"
    with sqlite3.connect(path) as connection:
        first.database.connection.backup(connection)
    path.chmod(0o600)
    shutil.copyfile(first.history.path, path.with_suffix(".protected-financial-witness.json"))
    path.with_suffix(".protected-financial-witness.json").chmod(0o600)
    database = Database(path)
    execution = Execution(database, Ledger(database, clock), clock, PaperBroker(database, clock))
    second = ProtectedFinancialService(database, clock, execution, manifest=target.manifest, capability_key=KEY)
    approved = proposal(second, first.database.file_identity(), operation_id="second-recovery")
    if interrupted:
        original_write = second.history._write_witness
        monkeypatch.setattr(second.history, "_write_witness", lambda *args: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError):
            operator(second).apply(approved)
        monkeypatch.setattr(second.history, "_write_witness", original_write)
        clock.advance(601)
    result = operator(second).apply(approved)
    assert operator(second).apply(approved) == result
    assert second.history.ready(pid)
    witness = json.loads(second.history.path.read_bytes())["payload"]
    assert len(witness["recoveries"]) == 2 and len(witness["transitions"]) == 1
    # Removing an ancestor receipt cannot preserve authority through the latest receipt.
    database.execute("DROP TRIGGER protected_financial_recoveries_no_delete")
    database.execute("DELETE FROM protected_financial_recoveries WHERE operation_id='synthetic-recovery'")
    assert not second.history.ready(pid)


def test_recovery_hard_fences_controllers_and_never_reissues_a_capability(tmp_path):
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    before = [dict(row) for row in recovered.database.execute(
        "SELECT instance_id,generation FROM protected_runtime_instances")]
    approved = proposal(recovered, identity)
    assert approved["authority_fences"]["instances"] == sorted(before, key=lambda row: row["instance_id"])
    operator(recovered).apply(approved)
    after = {row["instance_id"]: dict(row) for row in recovered.database.execute(
        "SELECT * FROM protected_runtime_instances")}
    assert all(after[row["instance_id"]]["generation"] == row["generation"] + 1 for row in before)
    assert all(row["status"] == "MANAGE_ONLY" for row in after.values())
    assert not recovered.database.execute("SELECT 1 FROM protected_rpc_requests WHERE state='ISSUED'").fetchone()


def test_changed_deployment_or_unapproved_source_manifest_cannot_gain_recovery_authority(tmp_path):
    from dataclasses import replace
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    wrong = ProtectedFinancialService(recovered.database, clock, recovered.execution,
        manifest=replace(recovered.manifest, deployment_id="different-deployment"), capability_key=KEY)
    with pytest.raises(AuthorityDenied, match="unchanged deployment"):
        operator(wrong).inspect(source_database_identity=identity,
            source_manifests=[asdict(old.financial.manifest)], operation_id="wrong-deployment",
            incident_evidence_sha256="a" * 64, expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
    changed = replace(recovered.manifest, wall_seconds=4)
    with pytest.raises(StaleState, match="all original financial scopes"):
        operator(recovered).inspect(source_database_identity=identity,
            source_manifests=[asdict(changed)], operation_id="wrong-source",
            incident_evidence_sha256="a" * 64, expires_at=utc_iso(clock.now() + timedelta(minutes=10)))


@pytest.mark.parametrize("attack", ["delete_attempt", "request_hash", "state", "cost", "usage", "delete_invocation"])
def test_recovered_legacy_subscription_rows_are_sealed_before_first_normal_checkpoint(tmp_path, monkeypatch, attack):
    from trade_graph.kernel.financial_checkpoint import FinancialHistoryCheckpoint
    from trade_graph.kernel.runtime_manifest import document_sha256
    original_scan = FinancialHistoryCheckpoint._scan
    def legacy_scan(self, *args, **kwargs):
        scan = original_scan(self, *args, **kwargs)
        scan["tables"].pop("subscription_invocations", None)
        scan["tables"].pop("subscription_attempts", None)
        scan["tables"]["attempts"].pop("identity_sha256", None)
        scan["state_sha256"] = document_sha256({key: scan[key] for key in ("state", "tables", "financial")})
        return scan
    with monkeypatch.context() as legacy:
        legacy.setattr(FinancialHistoryCheckpoint, "_scan", legacy_scan)
        original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path, subscriptions=True)
    approved = proposal(recovered, identity)
    assert "subscription_attempts" in approved["proof_limitations"][pid]["unwitnessed_tables"]
    operator(recovered).apply(approved)
    if attack == "delete_attempt":
        recovered.database.execute("DELETE FROM subscription_attempts WHERE rowid=1")
    elif attack == "delete_invocation":
        recovered.database.execute("DELETE FROM subscription_attempts WHERE invocation_id='retained-invocation-0'")
        recovered.database.execute("DELETE FROM subscription_invocations WHERE rowid=1")
    else:
        update = {"request_hash": "request_hash='changed'", "state": "state='COMPLETED'",
                  "cost": "actual_cost_native='0'", "usage": "usage_json='{}'"}[attack]
        recovered.database.execute(f"UPDATE subscription_attempts SET {update} WHERE rowid=1")
    assert not recovered.history.ready(pid)
    with pytest.raises(StaleState, match="(identity|terminal subscription)"):
        recovered.retain_budget_history(pid)


@pytest.mark.parametrize("table", ["valuation_marks", "fx_rates"])
@pytest.mark.parametrize("attack", ["delete", "change", "append"])
def test_recovery_append_prefix_preserves_marks_fx_and_accepts_new_random_ids(tmp_path, table, attack):
    from decimal import Decimal
    original, clock, pid, old, recovered, identity, original_witness = recovery_stack(tmp_path)
    ledger = recovered.ledger
    ledger.observe_mark(pid, "BTC", Decimal("100"), "USD", source="synthetic-recovery")
    ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"), source="synthetic-recovery",
        kind="close", stale=False)
    # These are newer than the original checkpoint; recovery establishes their first retention seal.
    operator(recovered).apply(proposal(recovered, identity))
    if attack == "delete":
        recovered.database.execute(f"DELETE FROM {table} WHERE rowid=(SELECT min(rowid) FROM {table})")
    elif attack == "change":
        field = "mark" if table == "valuation_marks" else "rate"
        recovered.database.execute(f"UPDATE {table} SET {field}='0' WHERE rowid=(SELECT min(rowid) FROM {table})")
    elif table == "valuation_marks":
        ledger.observe_mark(pid, "BTC", Decimal("101"), "USD", source="synthetic-new-mark")
    else:
        ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.91"), source="synthetic-new-fx",
            kind="close", stale=False)
    if attack == "append":
        recovered.retain_budget_history(pid)
        assert recovered.history.ready(pid)
    else:
        assert not recovered.history.ready(pid)
        with pytest.raises(StaleState, match="append-only financial prefix"):
            recovered.retain_budget_history(pid)
