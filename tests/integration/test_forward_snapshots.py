"""Retained report handoffs bind source revisions without inventing upstream trust."""

import json
import sqlite3
from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.integration.test_forward_evaluation import (
    OTHER_SHA,
    SHA,
    expense,
    populate,
    protocol,
    registry,
    seal,
)

from trade_graph.evaluation_contracts import (
    Attempt,
    AttemptResult,
    CostInventory,
    EvaluationSnapshot,
    ExpenseResolution,
)
from trade_graph.evaluation_registry import TrialRegistry, document_hash


def test_snapshot_binds_report_scope_inventory_and_all_sources_without_granting_trust(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "setup", "2", cost_class="setup_engineering")
    populate(instance, clock, declared)
    seal(instance, declared)
    snapshot = instance.snapshot(declared.trial_id)
    report = json.loads(snapshot.report_json)
    assert report["verdict"] == "supported"  # conditional mathematical fixture only
    assert snapshot.verification_basis == report["verification_basis"] == "unverified_imports"
    assert snapshot.portfolio_id == declared.portfolio_id
    assert snapshot.market_stream_id == declared.market_stream_id
    assert snapshot.selected_version_sha256 == declared.selected_version_sha256
    assert snapshot.protocol_sha256 == document_hash(declared.model_dump_json())
    assert snapshot.report_sha256 == document_hash(snapshot.report_json)
    assert snapshot.ledger_export_sha256 == SHA
    assert snapshot.source_cutoff == declared.forward_blocks[-1].end
    assert snapshot.inventory_sha256 is not None
    assert {item.kind for item in snapshot.source_records} == {"protocol", "expense", "allocation",
                                                             "observation", "inventory"}
    assert report["live_authorization"] is report["broader_engineer_authorization"] is False
    instance.verify_snapshot(snapshot)
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.report_sha256 = OTHER_SHA
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.source_records[0].document_sha256 = OTHER_SHA
    promoted = snapshot.model_dump()
    promoted["verification_basis"] = "authoritative_receipts_verified"
    with pytest.raises(ValidationError):
        EvaluationSnapshot.model_validate(promoted)


def test_pending_snapshot_is_retained_idempotently_and_verifies_after_reopen(tmp_path):
    instance, clock, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    assert json.loads(snapshot.report_json)["verdict"] == "insufficient_evidence"
    assert snapshot.inventory_sha256 is snapshot.ledger_export_sha256 is snapshot.source_cutoff is None
    assert instance.snapshot(declared.trial_id) == snapshot
    assert len(instance._rows("snapshot", declared.trial_id)) == 1
    instance.close()
    reopened = TrialRegistry(tmp_path / "forward.sqlite", clock)
    reopened.verify_snapshot(EvaluationSnapshot.model_validate_json(snapshot.model_dump_json()))


def test_report_edit_with_recomputed_digest_cannot_impersonate_retained_snapshot(tmp_path):
    instance, _, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    forged = snapshot.model_dump()
    report = json.loads(snapshot.report_json)
    report["verdict"] = "supported"
    forged["report_json"] = json.dumps(report, sort_keys=True, separators=(",", ":"))
    forged["report_sha256"] = document_hash(forged["report_json"])
    with pytest.raises(ValueError, match="not retained"):
        instance.verify_snapshot(EvaluationSnapshot.model_validate(forged))
    with pytest.raises(ValidationError, match="matching digest"):
        EvaluationSnapshot.model_validate({**snapshot.model_dump(), "report_json": forged["report_json"]})


def test_source_binding_and_as_of_edits_cannot_impersonate_retained_snapshot(tmp_path):
    instance, clock, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    forged = snapshot.model_dump()
    forged["source_records"][0]["document_sha256"] = OTHER_SHA
    with pytest.raises(ValidationError, match="manifest digest"):
        EvaluationSnapshot.model_validate(forged)
    later = snapshot.model_copy(update={"collected_as_of": clock.now() + timedelta(days=1)})
    with pytest.raises(ValueError, match="as-of must match"):
        instance.verify_snapshot(later)


def test_snapshot_cannot_verify_against_registry_that_did_not_emit_it(tmp_path):
    instance, clock, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    other = TrialRegistry(tmp_path / "other.sqlite", clock)
    other.register(declared)
    with pytest.raises(ValueError, match="not retained"):
        other.verify_snapshot(snapshot)


@pytest.mark.parametrize("field, value", [("portfolio_id", "different-paper-account"),
                                         ("selected_version_sha256", OTHER_SHA),
                                         ("protocol_sha256", OTHER_SHA)])
def test_snapshot_scope_and_protocol_fields_cannot_disagree_with_the_hashed_report(tmp_path, field, value):
    instance, _, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    with pytest.raises(ValidationError, match="protocol"):
        EvaluationSnapshot.model_validate({**snapshot.model_dump(), field: value})


def test_retained_snapshot_itself_is_append_only(tmp_path):
    instance, _, declared = registry(tmp_path)
    instance.snapshot(declared.trial_id)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        instance.connection.execute("DELETE FROM evaluation_records WHERE kind = 'snapshot'")


def test_semantically_equal_but_changed_retained_source_bytes_invalidate_snapshot(tmp_path):
    instance, _, declared = registry(tmp_path)
    snapshot = instance.snapshot(declared.trial_id)
    row = instance._rows("protocol", declared.trial_id)[0]
    differently_formatted = json.dumps(json.loads(row["document_json"]), indent=2)
    assert document_hash(differently_formatted) == row["document_sha256"]
    # Simulate corruption outside the append-only collector. A normal registry
    # writer cannot do this; exact retained byte bindings still detect it.
    instance.connection.execute("DROP TRIGGER evaluation_no_update")
    instance.connection.execute(
        "UPDATE evaluation_records SET document_json = ? WHERE kind = 'protocol'", (differently_formatted,),
    )
    with pytest.raises(ValueError, match="stale"):
        instance.verify_snapshot(snapshot)


def test_append_only_invoice_resolution_invalidates_old_snapshot_and_keeps_history(tmp_path):
    instance, clock, declared = registry(tmp_path)
    expense(instance, declared, "unknown", None, cost_class="setup_engineering", outcome="unresolved")
    populate(instance, clock, declared)
    seal(instance, declared)
    before = instance.snapshot(declared.trial_id)
    instance.verify_snapshot(before)
    assert "usage_or_invoice_cost_unresolved" in json.loads(before.report_json)["reasons"]
    instance.resolve_expense(ExpenseResolution(
        receipt_id="unknown", amount_eur="7", outcome="failed", source_ref="fixture:verified-invoice",
        conversion_ref="fixture:EUR-identity",
    ))
    with pytest.raises(ValueError, match="stale"):
        instance.verify_snapshot(before)
    after = instance.snapshot(declared.trial_id)
    instance.verify_snapshot(after)
    assert before.inventory_sha256 == after.inventory_sha256
    assert before.report_sha256 != after.report_sha256
    assert before.source_manifest_sha256 != after.source_manifest_sha256
    assert json.loads(before.report_json)["cost_inventory"]["unresolved_receipt_ids"] == ["unknown"]
    assert json.loads(after.report_json)["cost_inventory"]["unresolved_receipt_ids"] == []
    assert len(instance._rows("snapshot", declared.trial_id)) == 2
    assert after.verification_basis == "unverified_imports"


def test_new_family_trial_invalidates_prior_multiplicity_handoff(tmp_path):
    declared = protocol(maximum_family_trials=2)
    instance, _, _ = registry(tmp_path, declared)
    before = instance.snapshot(declared.trial_id)
    instance.register(protocol(trial_id="second", maximum_family_trials=2))
    with pytest.raises(ValueError, match="stale"):
        instance.verify_snapshot(before)
    after = instance.snapshot(declared.trial_id)
    assert json.loads(after.report_json)["trial_registry"]["family_trials"] == ["second", "trial"]
    instance.verify_snapshot(after)


def test_added_deployment_receipt_invalidates_complete_inventory_handoff(tmp_path):
    instance, clock, declared = registry(tmp_path)
    populate(instance, clock, declared)
    seal(instance, declared)
    before = instance.snapshot(declared.trial_id)
    from trade_graph.evaluation_contracts import ExpenseEvidence

    instance.record_expense(ExpenseEvidence(
        receipt_id="new-upstream-receipt", source_ref="fixture:late-discovered", conversion_ref="fixture:FX",
        incurred_at=declared.forward_blocks[0].start, amount_eur="1", evidence_kind="actual",
        cost_class="recurring", outcome="failed", variant_sha256=SHA,
    ))
    with pytest.raises(ValueError, match="stale"):
        instance.verify_snapshot(before)
    # A new report can bind the changed deployment registry, but cannot claim
    # whether this unallocated upstream receipt belongs to the sealed trial.
    after = instance.snapshot(declared.trial_id)
    assert after.verification_basis == "unverified_imports"
    assert after.source_manifest_sha256 != before.source_manifest_sha256


def test_unfinished_attempt_cannot_freeze_an_unrepairable_cost_inventory(tmp_path):
    instance, clock, declared = registry(tmp_path)
    instance.start_attempt(declared.trial_id, Attempt(
        attempt_id="failed-variant", variant_sha256=OTHER_SHA, objective="retain every outcome",
    ))
    populate(instance, clock, declared)
    with pytest.raises(ValueError, match="retained outcomes"):
        seal(instance, declared)
    assert not instance._rows("inventory", declared.trial_id)
    instance.finish_attempt(declared.trial_id, AttemptResult(
        attempt_id="failed-variant", outcome="failed", source_ref="fixture:local-failure", receipt_ids=(),
    ))
    seal(instance, declared)


def test_inventory_cutoff_cannot_precede_an_allocated_expense(tmp_path):
    instance, clock, declared = registry(tmp_path)
    populate(instance, clock, declared)
    clock.advance(timedelta(days=1).total_seconds())
    expense(instance, declared, "late-review", "2", cost_class="setup_engineering", incurred_at=clock.now())
    with pytest.raises(ValueError, match="every allocated expense"):
        seal(instance, declared)
    assert not instance._rows("inventory", declared.trial_id)
    instance.seal_cost_inventory(declared.trial_id, CostInventory(
        source_ref="fixture:post-review-export", ledger_export_sha256=SHA,
        source_cutoff=clock.now(), receipt_ids=("late-review",),
    ))
    assert instance.snapshot(declared.trial_id).source_cutoff == clock.now()
