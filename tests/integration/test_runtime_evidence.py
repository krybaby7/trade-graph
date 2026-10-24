"""Actual SQLite/ledger/budget paths with synthetic records, never paid evidence."""

import json
import sqlite3
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.integration.test_cost_acceptance import _reserve, _stack
from tests.integration.test_execution import _decision, _quote
from tests.integration.test_forward_evaluation import REGISTERED, protocol
from tests.integration.test_httpx_provider_failures import mock_stream, response
from tests.integration.test_ledger_store import _fill
from tests.integration.test_provider_transport import _request

from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.scheduler import Scheduler
from trade_graph.contracts.models import ModelUsage
from trade_graph.domain.clock import utc_iso
from trade_graph.evaluation_contracts import Attempt, ExpenseEvidence
from trade_graph.evaluation_registry import TrialRegistry, document_hash
from trade_graph.runtime_evidence import RuntimeEvidenceCapture, RuntimeEvidenceCollector


@pytest.fixture
def setup(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    collector.bind(declared.trial_id)
    yield runtime, registry, collector, declared
    registry.close()
    runtime.database.close()


def receipt(runtime, *, linked=True, synthetic=False):
    identity = runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
        source="synthetic runtime reference", kind="reference", stale=False)
    reservation = _reserve(runtime, fx_rate=Decimal("0.9"), fx_rate_id=identity if linked else None,
                           synthetic=synthetic)
    received = runtime.budget.commit(reservation,
        ModelUsage(uncached_input_tokens=100, billed_output_tokens=2,
                   provider_request_id=f"synthetic-provider:{reservation}"),
        provider="openai", model="gpt-6-luna", fx_rate=Decimal("0.9"))
    return identity, reservation, received


def test_collector_binds_before_forward_and_retains_actual_complete_db_inventory(setup):
    runtime, registry, collector, declared = setup
    binding = collector.bind(declared.trial_id)
    capture = collector.capture(declared.trial_id)
    verified = collector.verify(capture)
    assert binding.portfolio_id == runtime.portfolio_id
    assert json.loads(binding.initial_source_json)["ledger_events"]
    assert json.loads(capture.source_json)["journal_postings"]
    assert capture.evaluation_snapshot_sha256 == document_hash(capture.evaluation_snapshot.model_dump_json())
    assert verified.database_consistent is True
    assert verified.actual_external_provenance_verified is False
    assert "external_transport_provenance_missing" in verified.reasons
    assert "baseline_runtime_collection_missing" in verified.reasons
    assert verified.receipt_ids == ()
    assert (registry.snapshot(declared.trial_id).source_manifest_sha256
            == capture.evaluation_snapshot.source_manifest_sha256)


def test_capture_is_idempotent_under_fixed_clock_and_reopens_from_actual_db(setup):
    runtime, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    assert collector.capture(declared.trial_id) == captured
    assert len(registry._rows("runtime_capture", declared.trial_id)) == 1
    reopened_db = Database(runtime.database.path)
    reopened_registry = TrialRegistry(registry.connection.execute("PRAGMA database_list").fetchone()[2], runtime.clock)
    reopened = RuntimeEvidenceCollector(reopened_db, reopened_registry, runtime.clock, deployment_id="fixture")
    assert reopened.verify(captured).database_consistent is True
    reopened_registry.close()
    reopened_db.close()


def test_late_binding_cannot_relabel_preexisting_trial_as_collected(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id)
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    runtime.clock.advance((declared.forward_blocks[0].start - runtime.clock.now()).total_seconds())
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    with pytest.raises(ValueError, match="before untouched"):
        collector.bind(declared.trial_id)
    registry.close()
    runtime.database.close()


def test_collector_refuses_missing_binding_or_wrong_deployment(setup):
    runtime, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    other = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="other")
    with pytest.raises(ValueError, match="binding"):
        other.verify(captured)
    with pytest.raises(ValueError, match="bound before"):
        collector.capture("unknown-trial")


def test_runtime_cannot_be_replaced_by_identical_copy(setup, tmp_path):
    runtime, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    copy = tmp_path / "copy.sqlite"
    connection = sqlite3.connect(copy)
    runtime.database.connection.backup(connection)
    connection.close()
    other_db = Database(copy)
    other = RuntimeEvidenceCollector(other_db, registry, runtime.clock, deployment_id="fixture")
    with pytest.raises(ValueError, match="binding"):
        other.verify(captured)
    other_db.close()


def test_linked_receipts_are_recomputed_from_native_price_and_retained_exact_fx_source(setup):
    runtime, _, collector, declared = setup
    identity, reservation, received = receipt(runtime)
    captured = collector.capture(declared.trial_id)
    verified = collector.verify(captured)
    assert verified.database_consistent is True
    assert verified.receipt_ids == (received,)
    assert "receipt_fx_source_link_missing" not in verified.reasons
    assert "durable_provider_invocation_missing" in verified.reasons
    assert verified.actual_external_provenance_verified is False
    assert runtime.database.execute("SELECT fx_rate_id FROM usage_receipts").fetchone()[0] == identity
    assert reservation not in verified.unresolved_reservation_ids


def test_legacy_matching_rate_does_not_manufacture_fx_source_proof(setup):
    runtime, _, collector, declared = setup
    receipt(runtime, linked=False)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "receipt_fx_source_link_missing" in verified.reasons
    assert verified.actual_external_provenance_verified is False


def test_new_receipt_or_source_change_invalidates_retained_capture(setup):
    runtime, _, collector, declared = setup
    old = collector.capture(declared.trial_id)
    identity, _, _ = receipt(runtime)
    with pytest.raises(ValueError, match="stale"):
        collector.verify(old)
    current = collector.capture(declared.trial_id)
    runtime.database.execute("UPDATE fx_rates SET rate='0.8' WHERE rate_id=?", (identity,))
    with pytest.raises(ValueError, match="stale"):
        collector.verify(current)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:receipt_fx_source_changed" in verified.reasons
    assert verified.database_consistent is False


def test_forged_capture_with_recomputed_hash_is_not_retained(setup):
    _, _, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    body = json.loads(captured.source_json)
    body["usage_receipts"] = []
    body["deployment_budget"][0]["total_allowance"] = "100000"
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    forged = RuntimeEvidenceCapture.model_validate({**captured.model_dump(), "source_json": raw,
                                                    "source_sha256": document_hash(raw)})
    with pytest.raises(ValueError, match="not retained"):
        collector.verify(forged)
    with pytest.raises(ValidationError, match="digest mismatch"):
        RuntimeEvidenceCapture.model_validate({**captured.model_dump(), "source_json": raw})


def test_native_balanced_corruption_is_detected_by_event_replay(setup):
    runtime, _, collector, declared = setup
    runtime.database.execute("UPDATE journal_postings SET amount=CASE WHEN amount='100' THEN '99' ELSE '-99' END")
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:unbalanced_native_journal" not in verified.reasons
    assert "invalid:native_journal_does_not_match_event_replay" in verified.reasons
    assert verified.database_consistent is False


def test_unknown_paid_attempt_remains_unresolved_without_zero_cost_receipt(setup):
    runtime, _, collector, declared = setup
    reservation = _reserve(runtime)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert reservation in verified.unresolved_reservation_ids
    assert "unresolved_or_unreceipted_attempt" in verified.reasons
    assert collector.import_expenses(collector.capture(declared.trial_id)) == ()


def test_source_flags_never_authenticate_synthetic_receipts_or_transport(setup):
    runtime, _, collector, declared = setup
    _, _, received = receipt(runtime, synthetic=True)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert verified.synthetic_receipt_ids == (received,)
    assert verified.actual_external_provenance_verified is False
    assert "synthetic_runtime_receipts" in verified.reasons
    runtime.database.execute("UPDATE usage_receipts SET synthetic=0")
    changed = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:receipt_namespace_mismatch" in changed.reasons
    assert changed.actual_external_provenance_verified is False


def test_runtime_expenses_are_derived_not_caller_supplied_and_import_is_idempotent(setup):
    runtime, registry, collector, declared = setup
    _, _, received = receipt(runtime, synthetic=True)
    captured = collector.capture(declared.trial_id)
    assert collector.import_expenses(captured) == (received,)
    imported = registry.expenses()[0]
    assert imported.amount_eur == Decimal("0.0000936")
    assert imported.evidence_kind == "synthetic"
    assert imported.outcome == "unresolved"  # Usage alone cannot prove work succeeded.
    assert imported.source_ref.startswith("runtime-receipt:")
    assert imported.conversion_ref.startswith("runtime-fx:")
    with pytest.raises(ValueError, match="stale"):
        collector.verify(captured)
    assert collector.import_expenses(collector.capture(declared.trial_id)) == (received,)


def test_complete_inventory_bounds_refuse_pagination_or_cell_truncation(setup, monkeypatch):
    runtime, _, collector, declared = setup
    import trade_graph.runtime_evidence as module

    monkeypatch.setattr(module, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="complete collection bounds"):
        collector.capture(declared.trial_id)
    monkeypatch.setattr(module, "MAX_ROWS", 10000)
    runtime.database.execute("UPDATE deployment_budget SET currency=?", ("s" * 131073,))
    with pytest.raises(ValueError, match="cell exceeds"):
        collector.capture(declared.trial_id)


def test_current_registry_changes_invalidate_runtime_evaluation_binding(setup):
    runtime, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    registry.start_attempt(declared.trial_id, Attempt(
        attempt_id="new-variant", variant_sha256="b" * 64, objective="retained local attempt",
    ))
    with pytest.raises(ValueError, match="stale"):
        collector.verify(captured)


def test_missing_or_future_valuation_links_are_not_current_external_evidence(setup):
    runtime, _, collector, declared = setup
    identity, _, _ = receipt(runtime)
    runtime.database.execute("UPDATE fx_rates SET retrieved_at=? WHERE rate_id=?",
                            (utc_iso(declared.forward_blocks[-1].end), identity))
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:receipt_fx_source_changed" in verified.reasons
    assert verified.actual_external_provenance_verified is False


def test_same_receipt_id_cannot_hide_lower_imported_amount_or_changed_source_link(setup):
    runtime, registry, collector, declared = setup
    _, _, received = receipt(runtime, synthetic=True)
    registry.record_expense(ExpenseEvidence(
        receipt_id=received, source_ref="arbitrary-import", conversion_ref="arbitrary-FX",
        incurred_at=runtime.clock.now(), amount_eur=Decimal("0"), evidence_kind="actual",
        cost_class="recurring", outcome="succeeded",
    ))
    captured = collector.capture(declared.trial_id)
    verified = collector.verify(captured)
    assert "invalid:runtime_registry_expense_link" in verified.reasons
    assert verified.database_consistent is False
    with pytest.raises(ValueError, match="invalid runtime source"):
        collector.import_expenses(captured)


@pytest.mark.parametrize("table,column,value", [
    ("deployment_budget", "total_allowance", "9"),
    ("portfolios", "experiment_id", "changed-authority-footprint"),
])
def test_current_scope_or_financial_policy_changes_invalidate_capture(setup, table, column, value):
    runtime, _, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    runtime.database.execute(f"UPDATE {table} SET {column}=?", (value,))
    with pytest.raises(ValueError, match="stale"):
        collector.verify(captured)


def test_retained_capture_bytes_cannot_be_rewritten_under_original_key(setup):
    _, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    registry.connection.execute("DROP TRIGGER evaluation_no_update")
    registry.connection.execute("UPDATE evaluation_records SET document_json='{}',document_sha256=? "
                                "WHERE kind='runtime_capture'", (document_hash("{}"),))
    with pytest.raises(ValueError, match="not retained"):
        collector.verify(captured)
    with pytest.raises(ValueError, match="capture bytes changed"):
        collector.capture(declared.trial_id)


def test_shared_cost_allocation_cannot_duplicate_or_reduce_actual_receipt(setup):
    runtime, _, collector, declared = setup
    _, _, received = receipt(runtime)
    runtime.budget.allocate(received, {runtime.portfolio_id: Decimal("1")})
    assert collector.verify(collector.capture(declared.trial_id)).database_consistent is True
    runtime.database.execute("UPDATE cost_allocations SET weight='0.5'")
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:shared_runtime_expense_allocation" in verified.reasons
    assert "invalid:incomplete_or_duplicate_shared_allocation" in verified.reasons


def test_actual_native_fill_link_and_current_record_corruption_are_rechecked(setup):
    runtime, _, collector, declared = setup
    fill = _fill("runtime-fill", filled_at_utc=runtime.clock.now())
    runtime.ledger.apply_fill(runtime.portfolio_id, fill, base_asset="TEST", quote_asset="EUR")
    runtime.database.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)", (
        "source-fill", fill.venue, fill.account_id, fill.trade_id, runtime.portfolio_id,
        None, fill.model_dump_json(), utc_iso(runtime.clock.now()),
    ))
    captured = collector.capture(declared.trial_id)
    assert collector.verify(captured).database_consistent is True
    changed = fill.model_copy(update={"price": Decimal("41")})
    runtime.database.execute("UPDATE fills SET document_json=?", (changed.model_dump_json(),))
    with pytest.raises(ValueError, match="stale"):
        collector.verify(captured)
    assert "invalid:fill_native_ledger_link" in collector.verify(collector.capture(declared.trial_id)).reasons


def _wire_receipt(runtime, monkeypatch, *, before_response=None):
    task = Scheduler(runtime.database, runtime.clock).add_task(
        role="trader", objective="synthetic collector wire test", portfolio_id=runtime.portfolio_id,
        allocated_spend=Decimal("1"),
    )
    request = _request(task_id=task, root_task_id=task, context={}, max_tool_calls=0)
    fx = runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
                                  source="synthetic source", kind="reference", stale=False)
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=HttpxProviderHttp())
    def transport(*args, **options):
        if before_response:
            before_response()
        return response(429, {
            "id": "synthetic-wire-response", "model": request.model,
            "usage": {"input_tokens": 7, "output_tokens": 3}, "error": {"message": "fixture error"},
        })

    mock_stream(monkeypatch, transport)
    result = gateway.invoke(request, deployment_id="fixture", price_card_id="openai",
        fx_rate=Decimal("0.9"), fx_buffer=Decimal("1"), fx_rate_id=fx,
        invocation_id="synthetic-durable-wire", portfolio_id=runtime.portfolio_id)
    assert not result.ok and result.usage is not None
    return dict(runtime.database.execute("SELECT * FROM provider_transport_attempts").fetchone())


def test_protected_wire_observation_is_exactly_linked_but_never_authenticates_fixture(setup, monkeypatch):
    runtime, registry, collector, declared = setup
    wire = _wire_receipt(runtime, monkeypatch)
    runtime.database.execute("INSERT INTO version_history VALUES (?,?,?)",
                             (runtime.portfolio_id, "v", declared.selected_version_sha256))
    captured = collector.capture(declared.trial_id)
    verified = collector.verify(captured)
    assert verified.database_consistent is True
    assert verified.actual_external_provenance_verified is False
    assert verified.synthetic_receipt_ids == verified.receipt_ids
    assert not verified.unresolved_reservation_ids
    assert "provider_transport_attempt_missing" not in verified.reasons
    collector.import_expenses(captured)
    assert registry.expenses()[0].outcome == "failed"
    assert registry.expenses()[0].variant_sha256 == declared.selected_version_sha256
    runtime.database.execute("UPDATE provider_transport_attempts SET request_sha256=?", ("f" * 64,))
    assert wire["request_sha256"] != "f" * 64
    assert "invalid:provider_transport_request_link" in collector.verify(collector.capture(declared.trial_id)).reasons


@pytest.mark.parametrize("column,value,reason", [
    ("response_bytes", 1048577, "invalid:provider_transport_response_bounds"),
    ("finished_at", None, "invalid:provider_transport_complete_response"),
    ("invocation_id", "unrelated-attempt", "invalid:provider_transport_invocation_link"),
    ("synthetic", 0, "invalid:provider_transport_scope_or_chronology"),
])
def test_current_wire_bounds_scope_and_chronology_are_audited(setup, monkeypatch, column, value, reason):
    runtime, _, collector, declared = setup
    _wire_receipt(runtime, monkeypatch)
    before = collector.capture(declared.trial_id)
    runtime.database.execute(f"UPDATE provider_transport_attempts SET {column}=?", (value,))
    with pytest.raises(ValueError, match="stale"):
        collector.verify(before)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert reason in verified.reasons
    assert verified.database_consistent is False
    assert verified.actual_external_provenance_verified is False


def test_interrupted_wire_is_retained_and_unresolved_even_with_terminal_reservation(setup, monkeypatch):
    runtime, _, collector, declared = setup
    wire = _wire_receipt(runtime, monkeypatch)
    runtime.database.execute("UPDATE provider_transport_attempts SET outcome='UNCERTAIN',response_sha256=NULL")
    verified = collector.verify(collector.capture(declared.trial_id))
    assert wire["reservation_id"] in verified.unresolved_reservation_ids
    assert "provider_transport_outcome_unresolved" in verified.reasons


def test_sql_preflight_rejects_oversized_cells_before_selecting_payload(setup, monkeypatch):
    runtime, _, collector, declared = setup
    runtime.database.execute("UPDATE deployment_budget SET currency=?", ("x" * 131073,))
    original = runtime.database.execute

    def checked(sql, parameters=()):
        if sql.startswith("SELECT * FROM deployment_budget"):
            pytest.fail("oversized source payload was fetched before size preflight")
        return original(sql, parameters)

    monkeypatch.setattr(runtime.database, "execute", checked)
    with pytest.raises(ValueError, match="cell exceeds"):
        collector.capture(declared.trial_id)


def test_total_encoded_source_limit_rejects_whole_capture_before_appending(setup, monkeypatch):
    runtime, registry, collector, declared = setup
    import trade_graph.runtime_evidence as module

    before = len(registry._rows("runtime_capture", declared.trial_id))
    initial_size = len(collector.bind(declared.trial_id).initial_source_json.encode())
    runtime.database.execute("UPDATE deployment_budget SET currency=?", ("x" * 2048,))
    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", initial_size + 1000)
    with pytest.raises(ValueError, match="complete byte"):
        collector.capture(declared.trial_id)
    assert len(registry._rows("runtime_capture", declared.trial_id)) == before


def test_partial_fx_source_does_not_create_known_euro_cost(setup):
    runtime, registry, collector, declared = setup
    _, _, received = receipt(runtime)
    runtime.database.execute("UPDATE usage_receipts SET fx_rate_id=NULL")
    capture = collector.capture(declared.trial_id)
    assert "receipt_fx_source_link_missing" in collector.verify(capture).reasons
    assert collector.import_expenses(capture) == (received,)
    assert registry.expenses()[0].amount_eur is None
    assert registry.expenses()[0].outcome == "unresolved"


def test_protected_runtime_authority_state_is_part_of_current_inventory(setup):
    runtime, _, collector, declared = setup
    runtime.database.execute("INSERT INTO protected_runtime_instances VALUES (?,?,?,?,?,?,?)", (
        "synthetic-instance", "a" * 64, None, None, 0, "STOPPED", utc_iso(runtime.clock.now()),
    ))
    capture = collector.capture(declared.trial_id)
    assert json.loads(capture.source_json)["protected_runtime_instances"][0]["status"] == "STOPPED"
    runtime.database.execute("UPDATE protected_runtime_instances SET status='RUNNING'")
    with pytest.raises(ValueError, match="stale"):
        collector.verify(capture)


def _record_native_decision(runtime, declared):
    quote = _quote(runtime.clock, "100", "101")
    runtime.database.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", (
        quote.observation_id, quote.venue, quote.symbol, utc_iso(quote.event_time_utc),
        utc_iso(quote.available_at_utc), quote.model_dump_json(), 1,
    ))
    context = {"system_version_id": "v1", "market": {
        quote.symbol: {"observation": quote.model_dump(mode="json")},
    }}
    runtime.database.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)", (
        "snap", runtime.portfolio_id, utc_iso(runtime.clock.now()), json.dumps(context), utc_iso(runtime.clock.now()),
    ))
    runtime.database.execute("INSERT INTO version_history VALUES (?,?,?)", (
        runtime.portfolio_id, "v1", declared.selected_version_sha256,
    ))
    decision = _decision(runtime.clock, runtime.portfolio_id, action="hold")
    runtime.database.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)", (
        decision.record_id, runtime.portfolio_id, decision.action, decision.model_dump_json(),
        decision.mandate_revision, decision.policy_revision, decision.snapshot_id, decision.system_version_id,
        utc_iso(runtime.clock.now()), decision.task_id,
    ))


def test_point_in_time_decision_snapshot_and_protected_version_are_verified(setup):
    runtime, _, collector, declared = setup
    _record_native_decision(runtime, declared)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert verified.database_consistent is True
    assert "synthetic_or_paper_market_reference" in verified.reasons
    assert verified.actual_external_provenance_verified is False


@pytest.mark.parametrize("mutation,reason", [
    ("market", "invalid:decision_point_in_time_market_link"),
    ("snapshot", "invalid:decision_point_in_time_snapshot"),
    ("version", "invalid:decision_version_outside_preregistration"),
    ("observation", "invalid:point_in_time_market_observation"),
])
def test_actual_decision_input_time_links_and_version_are_not_caller_declarations(setup, mutation, reason):
    runtime, _, collector, declared = setup
    _record_native_decision(runtime, declared)
    if mutation == "market":
        retained = runtime.database.execute("SELECT payload_json FROM snapshots").fetchone()[0]
        context = json.loads(retained)
        context["market"]["BTC/USD"]["observation"]["observation_id"] = "absent-source"
        runtime.database.execute("UPDATE snapshots SET payload_json=?", (json.dumps(context),))
    elif mutation == "snapshot":
        runtime.database.execute("UPDATE snapshots SET as_of=?", (utc_iso(declared.forward_blocks[0].end),))
    elif mutation == "version":
        runtime.database.execute("UPDATE version_history SET artifact_hash=?", ("b" * 64,))
    else:
        runtime.database.execute("UPDATE observations SET available_at=?", (utc_iso(declared.forward_blocks[0].end),))
    verified = collector.verify(collector.capture(declared.trial_id))
    assert reason in verified.reasons
    assert verified.database_consistent is False


def test_balanced_orphan_postings_cannot_pass_native_transaction_ownership(setup):
    runtime, _, collector, declared = setup
    for identity, amount in (("orphan-positive", "1"), ("orphan-negative", "-1")):
        runtime.database.execute("INSERT INTO journal_postings VALUES (?,?,?,?,?,?)", (
            identity, "nonexistent-transaction", runtime.portfolio_id, "cash", "EUR", amount,
        ))
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:native_posting_transaction_ownership" in verified.reasons
    assert verified.database_consistent is False


@pytest.mark.parametrize("mutation,reason", [
    ("portfolio", "invalid:native_posting_transaction_ownership"),
    ("reference", "invalid:native_transaction_event_link"),
    ("kind", "invalid:native_transaction_event_link"),
    ("sequence", "invalid:native_event_sequence"),
])
def test_native_posting_owner_and_exact_event_metadata_must_agree(setup, mutation, reason):
    runtime, _, collector, declared = setup
    if mutation == "portfolio":
        runtime.database.execute("UPDATE journal_postings SET portfolio_id='nonexistent-portfolio'")
    elif mutation == "reference":
        runtime.database.execute("UPDATE journal_transactions SET external_ref='wrong-event'")
    elif mutation == "kind":
        runtime.database.execute("UPDATE journal_transactions SET kind='withdraw'")
    else:
        runtime.database.execute("UPDATE ledger_events SET sequence=2")
    verified = collector.verify(collector.capture(declared.trial_id))
    assert reason in verified.reasons
    assert verified.database_consistent is False


def test_postbinding_receipt_cannot_disappear_into_released_zero_cost_even_after_restart(setup):
    runtime, registry, collector, declared = setup
    _, reservation, received = receipt(runtime)
    collector.capture(declared.trial_id)
    runtime.database.execute("DELETE FROM usage_receipts WHERE receipt_id=?", (received,))
    runtime.database.execute("UPDATE budget_reservations SET state='RELEASED',amount='0' WHERE reservation_id=?",
                             (reservation,))
    reopened_db = Database(runtime.database.path)
    reopened_registry = TrialRegistry(registry.connection.execute("PRAGMA database_list").fetchone()[2], runtime.clock)
    reopened = RuntimeEvidenceCollector(reopened_db, reopened_registry, runtime.clock, deployment_id="fixture")
    captured = reopened.capture(declared.trial_id)
    verified = reopened.verify(captured)
    assert "invalid:captured_runtime_history_changed" in verified.reasons
    assert verified.database_consistent is False
    assert verified.actual_external_provenance_verified is False
    reopened_registry.close()
    reopened_db.close()


def test_pending_durable_attempt_can_legitimately_settle_without_history_rewrite(setup, monkeypatch):
    runtime, _, collector, declared = setup
    pending = []

    def retained_before_response():
        captured = collector.capture(declared.trial_id)
        verification = collector.verify(captured)
        assert verification.database_consistent is True
        assert len(verification.unresolved_reservation_ids) == 1
        pending.append(captured)

    _wire_receipt(runtime, monkeypatch, before_response=retained_before_response)
    assert len(pending) == 1
    current = collector.verify(collector.capture(declared.trial_id))
    assert current.database_consistent is True
    assert not current.unresolved_reservation_ids
    assert len(current.receipt_ids) == 1
    assert "invalid:captured_runtime_history_changed" not in current.reasons
    assert current.actual_external_provenance_verified is False


@pytest.mark.parametrize("mutation,reason", [
    ("arithmetic", "invalid:provider_invoice_arithmetic_currency_or_availability"),
    ("currency", "invalid:provider_invoice_arithmetic_currency_or_availability"),
    ("future", "invalid:provider_invoice_arithmetic_currency_or_availability"),
    ("cutoff", "invalid:provider_invoice_recorded_cost_cutoff"),
])
def test_impossible_invoice_arithmetic_currency_time_or_receipt_total_is_invalid(setup, mutation, reason):
    runtime, _, collector, declared = setup
    _, _, received = receipt(runtime)
    amount = Decimal(runtime.database.execute("SELECT reporting_cost FROM usage_receipts WHERE receipt_id=?",
                                              (received,)).fetchone()[0])
    runtime.clock.advance(1)
    runtime.budget.reconcile_invoice("fixture", "synthetic-consistency-invoice", amount)
    if mutation == "arithmetic":
        runtime.database.execute("UPDATE invoice_reconciliations SET invoice_total='100',unexplained='0'")
    elif mutation == "currency":
        runtime.database.execute("UPDATE invoice_reconciliations SET currency='USD'")
    elif mutation == "future":
        runtime.database.execute("UPDATE invoice_reconciliations SET created_at=?",
                                 (utc_iso(declared.forward_blocks[0].end),))
    else:
        runtime.database.execute("UPDATE invoice_reconciliations SET recorded_total='0',unexplained=?", (str(amount),))
    verified = collector.verify(collector.capture(declared.trial_id))
    assert reason in verified.reasons
    assert verified.database_consistent is False


def test_historical_invoice_cutoff_does_not_include_receipts_collected_later(setup):
    runtime, _, collector, declared = setup
    _, _, received = receipt(runtime)
    amount = Decimal(runtime.database.execute("SELECT reporting_cost FROM usage_receipts WHERE receipt_id=?",
                                              (received,)).fetchone()[0])
    runtime.clock.advance(1)
    runtime.budget.reconcile_invoice("fixture", "synthetic-consistency-invoice", amount)
    collector.capture(declared.trial_id)
    runtime.clock.advance(1)
    receipt(runtime)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert verified.database_consistent is True
    assert "provider_invoice_receipt_cutoff_ambiguous" not in verified.reasons
    assert "invalid:provider_invoice_recorded_cost_cutoff" not in verified.reasons


def test_same_clock_invoice_cutoff_is_pending_rather_than_invented_ordering(setup):
    runtime, _, collector, declared = setup
    _, _, received = receipt(runtime)
    amount = Decimal(runtime.database.execute("SELECT reporting_cost FROM usage_receipts WHERE receipt_id=?",
                                              (received,)).fetchone()[0])
    runtime.budget.reconcile_invoice("fixture", "synthetic-consistency-invoice", amount)
    verified = collector.verify(collector.capture(declared.trial_id))
    assert verified.database_consistent is True
    assert "provider_invoice_receipt_cutoff_ambiguous" in verified.reasons
    assert verified.actual_external_provenance_verified is False


@pytest.mark.parametrize("limit", ["MAX_HISTORY_RECORDS", "MAX_HISTORY_BYTES", "MAX_CAPTURE_BYTES"])
def test_history_verification_refuses_resource_overflow_without_truncating_sources(setup, monkeypatch, limit):
    _, registry, collector, declared = setup
    import trade_graph.runtime_evidence as module

    captured = collector.capture(declared.trial_id)
    before = len(registry._rows("runtime_capture", declared.trial_id))
    monkeypatch.setattr(module, limit, 0)
    with pytest.raises(ValueError, match="verification.*bounds"):
        collector.verify(captured)
    with pytest.raises(ValueError, match="verification.*bounds"):
        collector.clock.advance(1)
        collector.capture(declared.trial_id)
    assert len(registry._rows("runtime_capture", declared.trial_id)) == before


def test_future_initial_native_decisions_cannot_be_bound_as_untouched_future_evidence(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    runtime.clock.advance((declared.forward_blocks[0].start - runtime.clock.now()).total_seconds() + 10)
    _record_native_decision(runtime, declared)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    with pytest.raises(ValueError, match="future collected facts"):
        collector.bind(declared.trial_id)
    assert registry._rows("runtime_binding", declared.trial_id) == []
    registry.close()
    runtime.database.close()


def test_initial_immutable_fx_fact_cannot_change_before_the_first_capture(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    identity = runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
        source="synthetic initial reference", kind="reference", stale=False)
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    collector.bind(declared.trial_id)
    runtime.database.execute("UPDATE fx_rates SET rate='0.8' WHERE rate_id=?", (identity,))
    verified = collector.verify(collector.capture(declared.trial_id))
    assert "invalid:captured_runtime_history_changed" in verified.reasons
    assert verified.database_consistent is False
    registry.close()
    runtime.database.close()


def test_new_trial_cannot_erase_paid_facts_retained_by_another_trial_in_deployment_registry(setup):
    runtime, registry, collector, declared = setup
    _, reservation, received = receipt(runtime)
    collector.capture(declared.trial_id)
    runtime.database.execute("DELETE FROM usage_receipts WHERE receipt_id=?", (received,))
    runtime.database.execute("UPDATE budget_reservations SET state='RELEASED',amount='0' WHERE reservation_id=?",
                             (reservation,))
    next_trial = declared.model_copy(update={"trial_id": "second-trial-cannot-hide-cost", "family_id": "second-family"})
    registry.register(next_trial)
    collector.bind(next_trial.trial_id)
    verified = collector.verify(collector.capture(next_trial.trial_id))
    assert "invalid:captured_runtime_history_changed" in verified.reasons
    assert verified.database_consistent is False


def test_initial_paid_fact_retained_by_old_binding_cannot_disappear_without_any_capture(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    _, reservation, received = receipt(runtime)
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    collector.bind(declared.trial_id)
    assert registry._rows("runtime_capture") == []
    runtime.database.execute("DELETE FROM usage_receipts WHERE receipt_id=?", (received,))
    runtime.database.execute("UPDATE budget_reservations SET state='RELEASED',amount='0' WHERE reservation_id=?",
                             (reservation,))
    other = declared.model_copy(update={"trial_id": "later-trial", "family_id": "later-family"})
    registry.register(other)
    collector.bind(other.trial_id)
    verified = collector.verify(collector.capture(other.trial_id))
    assert "invalid:captured_runtime_history_changed" in verified.reasons
    assert verified.database_consistent is False
    registry.close()
    runtime.database.close()
