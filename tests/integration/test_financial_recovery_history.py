"""Accepted reporting gaps remain authenticated; financial recovery gates stay strict."""
import copy
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_financial_recovery import operator, recovery_stack

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState


def incident_for(financial, portfolio_id):
    now = financial.clock.now()
    portfolio = financial.database.execute("SELECT experiment_id FROM portfolios WHERE portfolio_id=?",
                                           (portfolio_id,)).fetchone()
    return {"schema_version": 1, "incident_id": "synthetic-partial-reporting-history", "portfolio_id": portfolio_id,
        "classification": "partial_price_reporting_history", "cause": "unknown",
        "backup_cutoff_at": utc_iso(now - timedelta(minutes=10)),
        "affected_interval": {"start_at": utc_iso(now - timedelta(minutes=10)), "end_at": utc_iso(now)},
        "missing_records": [
            {"table": "valuation_marks", "category": "valuation_mark", "record_id": "missing-synthetic-mark",
             "rowid": 15313, "observed_at": None},
            {"table": "observations", "category": "public_price_reporting_observation",
             "record_id": "missing-synthetic-public-price", "rowid": 100, "observed_at": None}],
        "unknown_additional_loss": True, "uncertainty_summary": "Exact historical values and times are unavailable.",
        "accepted_effect": "historical_reporting_only", "financial_ai_history_verified": True,
        "preserved_continuity_evidence_sha256": "b" * 64, "classification_evidence_sha256": "c" * 64,
        "previous_evaluation_period": {"period_id": portfolio["experiment_id"], "started_at": None},
        "new_evaluation_period": {"period_id": "synthetic-post-recovery-period", "started_at": utc_iso(now)},
        "old_run_retained": True, "account_reset": False}


def propose(financial, identity, incident):
    return operator(financial).inspect(source_database_identity=identity,
        source_manifests=[asdict(financial.manifest)], operation_id="partial-history-recovery",
        incident_evidence_sha256="a" * 64, history_incident=incident,
        expires_at=utc_iso(financial.clock.now() + timedelta(minutes=10)))


def test_partial_history_recovery_binds_gap_without_reset_replay_or_new_authority(tmp_path):
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path, subscriptions=True)
    incident = incident_for(financial, pid)
    before = {table: [dict(row) for row in financial.database.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("portfolios", "ledger_events", "journal_postings",
                      "subscription_invocations", "subscription_attempts")}
    approval = propose(financial, identity, incident)
    assert approval["schema_version"] == 2 and approval["history_incident"] == incident
    result = operator(financial).apply(approval, history_incident=approval["history_incident"])
    assert result["history_status"] == "PARTIAL_HISTORY"
    assert result["evaluation_period"] == incident["new_evaluation_period"]
    assert not result["paid_authorization"] and not result["live_authorization"]
    assert old.financial.history.path.read_bytes() == witness
    assert {table: [dict(row) for row in financial.database.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in before} == before
    projection = verified_recovery_history(financial, pid)
    assert projection["status"] == "PARTIAL_HISTORY"
    assert projection["incidents"][0]["incident"] == incident
    assert projection["active_evaluation_period"] == incident["new_evaluation_period"]
    assert projection["prior_evaluation_periods"] == [incident["previous_evaluation_period"]]
    assert projection["account_reset"] is False and projection["old_run_retained"] is True
    assert financial.execution.profile(pid) == "MANAGE_ONLY"
    assert operator(financial).apply(approval, history_incident=approval["history_incident"]) == result


@pytest.mark.parametrize("attack", ["ledger", "attempts", "ai_inputs", "category", "future", "reset", "old_run",
                                    "period_identity", "period_start", "audit", "existing_record",
                                    "duplicate", "malformed"])
def test_partial_history_rejects_widened_scope_and_unverified_incident(tmp_path, attack):
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    incident = incident_for(financial, pid)
    if attack == "malformed":
        incident = []
    elif attack in {"ledger", "attempts", "ai_inputs"}:
        incident["missing_records"][0]["table"] = {"ledger": "ledger_events", "attempts": "subscription_attempts",
                                                  "ai_inputs": "snapshots"}[attack]
    elif attack == "category":
        incident["missing_records"][1]["category"] = "model_decision_input"
    elif attack == "future":
        incident["affected_interval"]["end_at"] = utc_iso(clock.now() + timedelta(seconds=1))
    elif attack == "reset":
        incident["account_reset"] = True
    elif attack == "old_run":
        incident["old_run_retained"] = False
    elif attack == "period_identity":
        incident["new_evaluation_period"]["period_id"] = incident["previous_evaluation_period"]["period_id"]
    elif attack == "period_start":
        incident["new_evaluation_period"]["started_at"] = incident["backup_cutoff_at"]
    elif attack == "audit":
        incident["financial_ai_history_verified"] = False
    elif attack == "existing_record":
        mark = financial.ledger.observe_mark(pid, "BTC", Decimal("100"), "USD", source="synthetic")
        incident["missing_records"][0]["record_id"] = mark
    else:
        incident["missing_records"].append(copy.deepcopy(incident["missing_records"][0]))
    with pytest.raises((AuthorityDenied, StaleState, ValueError)):
        propose(financial, identity, incident)
    assert financial.history.path.read_bytes() == witness
    assert financial.database.execute("SELECT count(*) FROM protected_financial_recoveries").fetchone()[0] == 0


def test_changed_incident_and_lost_attempt_still_refuse(tmp_path):
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path, subscriptions=True)
    approval = propose(financial, identity, incident_for(financial, pid))
    changed = copy.deepcopy(approval)
    changed["history_incident"]["uncertainty_summary"] = "changed after inspection"
    with pytest.raises(AuthorityDenied, match="identical"):
        operator(financial).apply(changed, history_incident=approval["history_incident"])
    financial.database.execute("DELETE FROM subscription_attempts WHERE rowid=1")
    with pytest.raises(StaleState, match="identity"):
        operator(financial).apply(approval, history_incident=approval["history_incident"])
    assert financial.history.path.read_bytes() == witness


def test_incident_tamper_or_witness_rewind_cannot_hide_gap(tmp_path):
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    approved = propose(financial, identity, incident_for(financial, pid))
    operator(financial).apply(approved, history_incident=approved["history_incident"])
    financial.history.path.write_bytes(witness)
    with pytest.raises(StaleState):
        verified_recovery_history(financial, pid)


def test_original_v1_recovery_remains_valid_without_inventing_a_complete_history_claim(tmp_path):
    from tests.integration.test_financial_recovery import proposal

    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    approved = proposal(financial, identity)
    assert approved["schema_version"] == 1 and "history_incident" not in approved
    operator(financial).apply(approved, history_incident=approved.get("history_incident"))
    result = verified_recovery_history(financial, pid)
    assert result["status"] == "NO_RECORDED_INCIDENT" and result["incidents"] == []
    assert result["active_evaluation_period"] is None


@pytest.mark.parametrize("published", [False, True])
def test_partial_incident_publication_is_idempotent_after_expiry(tmp_path, monkeypatch, published):
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    approved = propose(financial, identity, incident_for(financial, pid))
    original_write = financial.history._write_witness
    def interrupted(directory, payload):
        if published:
            original_write(directory, payload)
        raise OSError("synthetic publication failure")
    monkeypatch.setattr(financial.history, "_write_witness", interrupted)
    with pytest.raises(OSError):
        operator(financial).apply(approved, history_incident=approved.get("history_incident"))
    monkeypatch.setattr(financial.history, "_write_witness", original_write)
    clock.advance(601)
    result = operator(financial).apply(approved, history_incident=approved.get("history_incident"))
    assert operator(financial).apply(approved, history_incident=approved.get("history_incident")) == result
    assert len(verified_recovery_history(financial, pid)["incidents"]) == 1


def test_receipt_incident_tamper_fails_authenticated_projection(tmp_path):
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    approved = propose(financial, identity, incident_for(financial, pid))
    operator(financial).apply(approved, history_incident=approved["history_incident"])
    financial.database.execute("DROP TRIGGER protected_financial_recoveries_no_update")
    financial.database.execute("UPDATE protected_financial_recoveries SET payload_json=replace(payload_json,?,?)",
        ("Exact historical values and times are unavailable.", "Tampered historical claim."))
    with pytest.raises(StaleState):
        verified_recovery_history(financial, pid)


def test_partial_period_lineage_follows_witness_append_order_for_equal_timestamps(tmp_path):
    import shutil
    import sqlite3

    from trade_graph.adapters.brokers.paper import PaperBroker
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.execution import Execution
    from trade_graph.application.ledger import Ledger
    from trade_graph.kernel.financial_service import ProtectedFinancialService
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, first, identity, witness = recovery_stack(tmp_path)
    approved = propose(first, identity, incident_for(first, pid))
    operator(first).apply(approved, history_incident=approved["history_incident"])
    candidate = tmp_path / "second"
    candidate.mkdir(mode=0o700)
    path = candidate / "protected.sqlite"
    with sqlite3.connect(path) as connection:
        first.database.connection.backup(connection)
    path.chmod(0o600)
    copied_witness = path.with_suffix(".protected-financial-witness.json")
    shutil.copyfile(first.history.path, copied_witness)
    copied_witness.chmod(0o600)
    database = Database(path)
    execution = Execution(database, Ledger(database, clock), clock, PaperBroker(database, clock))
    second = ProtectedFinancialService(database, clock, execution, manifest=first.manifest,
        capability_key=first._capability_key)
    incident = incident_for(second, pid)
    incident["incident_id"] = "second-incident"
    incident["backup_cutoff_at"] = utc_iso(clock.now())
    incident["affected_interval"]["start_at"] = utc_iso(clock.now())
    incident["previous_evaluation_period"] = approved["history_incident"]["new_evaluation_period"]
    incident["new_evaluation_period"]["period_id"] = "second-post-recovery-period"
    next_approval = operator(second).inspect(source_database_identity=first.database.file_identity(),
        source_manifests=[asdict(second.manifest)], operation_id="a-lexically-earlier-recovery",
        incident_evidence_sha256="d" * 64, history_incident=incident,
        expires_at=utc_iso(clock.now() + timedelta(minutes=10)))
    operator(second).apply(next_approval, history_incident=incident)
    projected = verified_recovery_history(second, pid)
    assert len(projected["incidents"]) == 2
    assert projected["active_evaluation_period"] == incident["new_evaluation_period"]
    assert projected["prior_evaluation_periods"][-1] == incident["previous_evaluation_period"]


def test_fresh_observation_may_reuse_lost_source_rowid_without_claiming_payload_recovery(tmp_path):
    from trade_graph.kernel.recovery_history import verified_recovery_history
    original, clock, pid, old, financial, identity, witness = recovery_stack(tmp_path)
    fresh = financial.ledger.observe_mark(pid, "BTC", Decimal("100"), "USD", source="fresh-public-test")
    rowid = financial.database.execute("SELECT rowid FROM valuation_marks WHERE mark_id=?", (fresh,)).fetchone()[0]
    incident = incident_for(financial, pid)
    incident["missing_records"][0]["rowid"] = rowid
    assert incident["missing_records"][0]["record_id"] != fresh
    approved = propose(financial, identity, incident)
    operator(financial).apply(approved, history_incident=incident)
    projected = verified_recovery_history(financial, pid)
    assert projected["incidents"][0]["incident"]["missing_records"][0]["rowid"] == rowid
    assert financial.database.execute("SELECT count(*) FROM valuation_marks").fetchone()[0] == 1
    assert financial.database.execute("SELECT mark_id FROM valuation_marks").fetchone()[0] == fresh
