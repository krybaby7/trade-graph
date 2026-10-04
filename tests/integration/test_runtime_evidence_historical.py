"""Retained historical reports survive later facts without becoming current."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_runtime_evidence import receipt
from tests.integration.test_runtime_evidence import setup as runtime_setup

from trade_graph.domain.clock import utc_iso
from trade_graph.evaluation_contracts import Attempt, ExpenseAllocation, ExpenseEvidence
from trade_graph.evaluation_registry import document_hash
from trade_graph.runtime_evidence import (
    SCHEMA_V1_TABLES,
    RuntimeCollectionBinding,
    RuntimeEvidenceCapture,
    _canonical,
)


@pytest.fixture(name="setup")
def historical_setup(tmp_path):
    yield from runtime_setup.__wrapped__(tmp_path)


def test_historical_runtime_capture_survives_later_paid_fact_and_keeps_current_gate_stale(setup):
    runtime, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    before = collector.verify_historical(captured)
    assert before.is_current_capture is True
    runtime.clock.advance(1)
    receipt(runtime, synthetic=True)
    result = collector.verify_historical(captured)
    assert result.retained_history_consistent is True
    assert result.is_current_capture is False
    assert result.report_sha256 == before.report_sha256
    assert result.current_source_sha256 != before.current_source_sha256
    assert result.actual_external_provenance_verified is False
    assert "later_runtime_evidence_present" in result.reasons
    with pytest.raises(ValueError, match="stale"):
        collector.verify(captured)
    registry.verify_historical_snapshot(captured.evaluation_snapshot)


def test_frozen_historical_report_retains_negative_attempt_and_cost_even_after_new_costs(setup):
    runtime, registry, collector, declared = setup
    _, _, first = receipt(runtime, synthetic=True)
    collector.import_expenses(collector.capture(declared.trial_id))
    registry.start_attempt(
        declared.trial_id,
        Attempt(attempt_id="negative-variant", variant_sha256="b" * 64, objective="retain unsuccessful earlier work"),
    )
    registry.allocate_expense(
        declared.trial_id,
        ExpenseAllocation(receipt_id=first, arm="agent", weight="1", source_ref="protected fixed allocation"),
    )
    captured = collector.capture(declared.trial_id)
    raw_report = captured.evaluation_snapshot.report_json
    assert first in raw_report and "negative-variant" in raw_report
    runtime.clock.advance(1)
    receipt(runtime, synthetic=True)
    collector.import_expenses(collector.capture(declared.trial_id))
    result = collector.verify_historical(captured)
    assert result.retained_history_consistent is True
    assert result.is_current_capture is False
    assert "later_registry_evidence_requires_current_economic_report" in result.reasons
    assert captured.evaluation_snapshot.report_json == raw_report
    assert registry.snapshot(declared.trial_id).report_json != raw_report
    with pytest.raises(ValueError, match="stale"):
        registry.verify_snapshot(captured.evaluation_snapshot)


def test_historical_verification_accepts_closed_paper_portfolio_but_current_verification_refuses(setup):
    runtime, _, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    runtime.database.execute("UPDATE portfolios SET status='closed' WHERE portfolio_id=?", (runtime.portfolio_id,))
    assert collector.verify_historical(captured).retained_history_consistent is True
    with pytest.raises(ValueError, match="open paper"):
        collector.verify(captured)


def test_historical_immutable_fact_corruption_remains_invalid(setup):
    runtime, _, collector, declared = setup
    receipt(runtime, synthetic=True)
    captured = collector.capture(declared.trial_id)
    runtime.database.execute("UPDATE fx_rates SET source='rewritten source'")
    result = collector.verify_historical(captured)
    assert result.retained_history_consistent is False
    assert "invalid:captured_runtime_history_changed" in result.reasons


def test_frozen_expense_import_cannot_misstate_actual_retained_runtime_receipt(setup):
    runtime, registry, collector, declared = setup
    _, _, receipt_id = receipt(runtime, synthetic=True)
    registry.record_expense(
        ExpenseEvidence(
            receipt_id=receipt_id,
            source_ref="forged-source",
            conversion_ref="forged-conversion",
            incurred_at=runtime.clock.now(),
            amount_eur="2",
            evidence_kind="synthetic",
            cost_class="recurring",
            outcome="unresolved",
        )
    )
    capture = collector.capture(declared.trial_id)
    assert "invalid:runtime_registry_expense_link" in collector.verify(capture).reasons
    historical = collector.verify_historical(capture)
    assert historical.retained_history_consistent is False
    assert "invalid:runtime_registry_expense_link" in historical.reasons


@pytest.mark.parametrize("changed", ["bytes", "timestamp", "trial"])
def test_historical_registry_source_retained_metadata_and_exact_bytes_are_required(setup, changed):
    _, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    registry.connection.execute("DROP TRIGGER evaluation_no_update")
    if changed == "bytes":
        row = registry.connection.execute(
            "SELECT document_json FROM evaluation_records WHERE kind='protocol'"
        ).fetchone()
        registry.connection.execute(
            "UPDATE evaluation_records SET document_json=? WHERE kind='protocol'",
            (json.dumps(json.loads(row[0]), indent=2),),
        )
    elif changed == "timestamp":
        registry.connection.execute(
            "UPDATE evaluation_records SET collected_at='2026-01-01T00:00:00+00:00' WHERE kind='protocol'"
        )
    else:
        registry.connection.execute("UPDATE evaluation_records SET trial_id='wrong-trial' WHERE kind='protocol'")
    with pytest.raises(ValueError, match="source bytes or metadata changed"):
        registry.verify_historical_snapshot(captured.evaluation_snapshot)


def test_historical_missing_report_is_refused(setup):
    _, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    registry.connection.execute("DROP TRIGGER evaluation_no_delete")
    registry.connection.execute("DELETE FROM evaluation_records WHERE kind='snapshot'")
    with pytest.raises(ValueError, match="not retained"):
        registry.verify_historical_snapshot(captured.evaluation_snapshot)


@pytest.mark.parametrize("limit", ["MAX_HISTORICAL_SOURCE_RECORDS", "MAX_HISTORICAL_SOURCE_BYTES"])
def test_historical_source_count_and_byte_bounds_are_enforced_before_reconstruction(setup, monkeypatch, limit):
    import trade_graph.evaluation_registry as module

    _, registry, collector, declared = setup
    captured = collector.capture(declared.trial_id)
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(ValueError, match="verification bounds"):
        registry.verify_historical_snapshot(captured.evaluation_snapshot)


def test_schema_one_history_retains_original_inventory_contract_without_silent_reseal(setup):
    _, registry, collector, declared = setup
    current = collector._binding(declared.trial_id)
    raw = _canonical(
        {key: value for key, value in json.loads(current.initial_source_json).items() if key in SCHEMA_V1_TABLES}
    )
    legacy = RuntimeCollectionBinding.model_validate(
        {
            **current.model_dump(),
            "schema_version": 1,
            "initial_source_json": raw,
            "initial_source_sha256": document_hash(raw),
        }
    )
    registry.connection.execute("DROP TRIGGER evaluation_no_update")
    registry.connection.execute(
        "UPDATE evaluation_records SET document_json=?, document_sha256=? WHERE kind='runtime_binding'",
        (legacy.model_dump_json(), document_hash(legacy.model_dump_json())),
    )
    snapshot = registry.snapshot(declared.trial_id)
    capture = RuntimeEvidenceCapture(
        schema_version=1,
        binding=legacy,
        captured_at=collector.clock.now(),
        source_json=raw,
        source_sha256=document_hash(raw),
        evaluation_snapshot=snapshot,
        evaluation_snapshot_sha256=document_hash(snapshot.model_dump_json()),
    )
    with registry._atomic():
        registry._append("runtime_capture", document_hash(capture.model_dump_json()), declared.trial_id, capture)
    assert collector.verify_historical(capture).retained_history_consistent is True
    with pytest.raises(ValueError, match="new preregistered authority inventory"):
        collector.capture(declared.trial_id)


def test_current_inventory_contains_protected_authority_state_and_preserves_immutable_checkpoint_history(setup):
    runtime, _, collector, declared = setup
    previous = collector.capture(declared.trial_id)
    assert previous.schema_version == 2
    data = json.loads(previous.source_json)
    assert {
        "live_pilot_grants",
        "live_pilot_effects",
        "live_pilot_events",
        "native_incident_resolutions",
        "native_incident_resolution_revocations",
        "protected_financial_checkpoints",
    } <= set(data)
    runtime.database.execute(
        "INSERT INTO protected_financial_checkpoints VALUES (?,?,?,?,?,?,?)",
        ("synthetic-checkpoint", "a" * 64, runtime.portfolio_id, 1, "{}", "b" * 64, runtime.ledger.now()),
    )
    with pytest.raises(ValueError, match="stale"):
        collector.verify(previous)
    current = collector.capture(declared.trial_id)
    assert collector.verify(current).database_consistent is True
    assert (
        "protected_financial_checkpoint_authentication_requires_independent_verifier"
        in collector.verify(current).reasons
    )
    runtime.database.execute("DROP TRIGGER protected_financial_checkpoints_no_update")
    runtime.database.execute("UPDATE protected_financial_checkpoints SET payload_json='{} '")
    assert collector.verify_historical(current).retained_history_consistent is False


def test_actual_append_only_chronological_fill_replay_is_audited_against_exact_original_sources(setup):
    from tests.integration.test_append_only_fill_replay import _fill

    runtime, _, collector, declared = setup
    runtime.ledger.deposit(runtime.portfolio_id, "USD", Decimal("1000"), "synthetic-native-capital")
    runtime.ledger.observe_fx(
        base="USD", quote="EUR", rate=Decimal("0.9"), source="synthetic-native-fx", kind="reference", stale=False
    )
    native_origin = runtime.clock.now()
    runtime.clock.advance(4)

    def retain(fill, *, late=False):
        method = runtime.ledger.apply_late_fill if late else runtime.ledger.apply_fill
        method(runtime.portfolio_id, fill, base_asset="BTC", quote_asset="USD")
        runtime.database.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
            (
                fill.trade_id,
                fill.venue,
                fill.account_id,
                fill.trade_id,
                runtime.portfolio_id,
                fill.intent_id,
                fill.model_dump_json(),
                utc_iso(runtime.clock.now()),
            ),
        )

    retain(
        _fill("known-buy", minute=2, price="200").model_copy(
            update={"filled_at_utc": native_origin + timedelta(seconds=2)}
        )
    )
    retain(
        _fill("known-sale", minute=3, side="sell", price="300").model_copy(
            update={"filled_at_utc": native_origin + timedelta(seconds=3)}
        )
    )
    previous = collector.capture(declared.trial_id)
    assert collector.verify(previous).database_consistent is True
    runtime.clock.advance(1)
    retain(
        _fill("earlier-late-buy", minute=1).model_copy(update={"filled_at_utc": native_origin + timedelta(seconds=1)}),
        late=True,
    )
    current = collector.capture(declared.trial_id)
    assert collector.verify(current).database_consistent is True
    assert collector.verify_historical(previous).retained_history_consistent is True
    rows = list(runtime.database.execute("SELECT * FROM ledger_events WHERE kind='fill_chronological_replay'"))
    assert len(rows) == 1
    runtime.database.execute(
        "DELETE FROM journal_postings WHERE transaction_id IN "
        "(SELECT transaction_id FROM journal_transactions WHERE kind='fill_chronological_replay')"
    )
    assert collector.verify(collector.capture(declared.trial_id)).database_consistent is False
