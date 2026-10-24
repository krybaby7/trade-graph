"""Prospective lossless retention: real SQLite paths, synthetic expenses only."""

import json
import sqlite3
import zlib
from hashlib import sha256

import pytest
from tests.integration.test_cost_acceptance import _stack
from tests.integration.test_forward_evaluation import REGISTERED, protocol
from tests.integration.test_runtime_evidence import receipt

from trade_graph.adapters.persistence.db import Database
from trade_graph.evaluation_contracts import Attempt, AttemptResult
from trade_graph.evaluation_registry import TrialRegistry, document_hash
from trade_graph.runtime_evidence import RuntimeEvidenceCollector
from trade_graph.runtime_evidence_archive import ArchivedRuntimeCapture, expand_capture


@pytest.fixture
def archived(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    registry.register(declared)
    collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock,
                                         deployment_id="fixture", history_storage="lossless_zlib")
    collector.bind(declared.trial_id)
    yield runtime, registry, collector, declared
    registry.close()
    runtime.database.close()


def test_archive_retains_more_than_legacy_history_and_restarts_without_erasing_costs_or_negative_work(archived):
    runtime, registry, collector, declared = archived
    _, reservation, paid = receipt(runtime, synthetic=True)
    early = collector.capture(declared.trial_id)
    collector.import_expenses(early)
    registry.start_attempt(declared.trial_id, Attempt(
        attempt_id="negative-candidate", variant_sha256="b" * 64, objective="failed synthetic candidate",
    ))
    registry.finish_attempt(declared.trial_id, AttemptResult(
        attempt_id="negative-candidate", outcome="failed", source_ref="synthetic:failed-attempt",
        receipt_ids=(paid,),
    ))
    originals = {}
    # Genuine captures at distinct collector times exceed the previous 256 total
    # records. Retention is exercised through the API rather than inserted mocks.
    for _ in range(256):
        runtime.clock.advance(1)
        latest = collector.capture(declared.trial_id)
        originals[document_hash(latest.model_dump_json())] = latest.model_dump_json()
    assert len(registry._rows("runtime_capture")) == 257
    source_kinds = {source.kind for source in latest.evaluation_snapshot.source_records}
    assert {"runtime_history_policy", "attempt", "attempt_result", "expense"} <= source_kinds
    with pytest.raises(ValueError, match="stale"):
        collector.verify(early)

    reopened_db = Database(runtime.database.path)
    reopened_registry = TrialRegistry(registry.connection.execute("PRAGMA database_list").fetchone()[2], runtime.clock)
    reopened = RuntimeEvidenceCollector(reopened_db, reopened_registry, runtime.clock,
                                       deployment_id="fixture", history_storage="lossless_zlib")
    try:
        assert reopened.verify(latest).database_consistent is True
        assert reopened.verify(latest).actual_external_provenance_verified is False
        assert len(reopened_registry.expenses()) == 1
        assert reopened_registry.expenses()[0].receipt_id == paid
        report = reopened_registry.report(declared.trial_id)
        assert report["verdict"] == "insufficient_evidence"
        assert reopened_registry._rows("attempt_result")[0]["document_json"].find('"outcome":"failed"') >= 0
        # Every historical capture's exact full bytes remain recoverable.
        for key, raw in originals.items():
            assert reopened._retained_capture(key, declared.trial_id)["document_json"] == raw
        runtime.database.execute("DELETE FROM usage_receipts WHERE receipt_id=?", (paid,))
        runtime.database.execute("UPDATE budget_reservations SET state='RELEASED',amount='0' WHERE reservation_id=?",
                                 (reservation,))
        checked = reopened.verify(reopened.capture(declared.trial_id))
        assert checked.database_consistent is False
        assert "invalid:captured_runtime_history_changed" in checked.reasons
        assert reopened_registry.expenses()[0].receipt_id == paid
    finally:
        reopened_registry.close()
        reopened_db.close()


def test_storage_policy_is_pinned_before_any_trial_and_cannot_switch_or_expand_afterward(archived):
    runtime, registry, collector, declared = archived
    recorded = registry._rows("runtime_history_policy")
    assert len(recorded) == 1
    policy = json.loads(recorded[0]["document_json"])
    assert policy["declared_at"] <= collector.bind(declared.trial_id).bound_at.isoformat().replace("+00:00", "Z")
    inline = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
    with pytest.raises(ValueError, match="policy binding"):
        inline.capture(declared.trial_id)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry.connection.execute("UPDATE evaluation_records SET document_json='{}' "
                                    "WHERE kind='runtime_history_policy'")
    collector.capture(declared.trial_id)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry.connection.execute("DELETE FROM runtime_capture_blobs")


def test_existing_inline_trial_cannot_gain_archive_limits_even_before_its_first_capture(tmp_path):
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    try:
        registry.register(declared)
        inline = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock, deployment_id="fixture")
        inline.bind(declared.trial_id)
        archived = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock,
                                            deployment_id="fixture", history_storage="lossless_zlib")
        with pytest.raises(ValueError, match="policy binding"):
            archived.bind(declared.trial_id)
        assert registry._rows("runtime_capture") == []
        assert inline.verify(inline.capture(declared.trial_id)).database_consistent is True
    finally:
        registry.close()
        runtime.database.close()


@pytest.mark.parametrize("mutation", ["missing", "bytes", "manifest"])
def test_missing_or_changed_archive_objects_cannot_be_replaced_by_current_consistent_source(archived, mutation):
    runtime, registry, collector, declared = archived
    retained = collector.capture(declared.trial_id)
    runtime.clock.advance(1)
    current = collector.capture(declared.trial_id)
    row = registry._rows("runtime_capture")[0]
    manifest = json.loads(row["document_json"])
    registry.connection.execute("DROP TRIGGER runtime_capture_blob_no_update")
    registry.connection.execute("DROP TRIGGER runtime_capture_blob_no_delete")
    if mutation == "missing":
        registry.connection.execute("DELETE FROM runtime_capture_blobs WHERE compressed_sha256=?",
                                    (manifest["compressed_sha256"],))
    elif mutation == "bytes":
        registry.connection.execute("UPDATE runtime_capture_blobs SET compressed_data=? WHERE compressed_sha256=?",
                                    (b"invalid private evidence", manifest["compressed_sha256"]))
    else:
        registry.connection.execute("DROP TRIGGER evaluation_no_update")
        manifest["expanded_sha256"] = "0" * 64
        changed = json.dumps(manifest, separators=(",", ":"))
        registry.connection.execute("UPDATE evaluation_records SET document_json=?,document_sha256=? "
                                    "WHERE kind='runtime_capture' AND record_key=?",
                                    (changed, document_hash(changed), row["record_key"]))
    with pytest.raises(ValueError, match="archive|archived|digest|storage"):
        collector.verify(current)
    assert len(registry._rows("runtime_capture")) == 2
    assert document_hash(retained.model_dump_json()) != document_hash(current.model_dump_json())


def test_decompression_bomb_and_trailing_or_truncated_streams_fail_with_a_bounded_output():
    raw = b"private synthetic evidence" * 100000
    compressed = zlib.compress(raw)

    def manifest(payload, expanded_size):
        return ArchivedRuntimeCapture(capture_sha256="a" * 64, expanded_sha256=sha256(raw).hexdigest(),
                                      compressed_sha256=sha256(payload).hexdigest(), expanded_bytes=expanded_size,
                                      compressed_bytes=len(payload))

    with pytest.raises(ValueError, match="expansion.*byte bounds"):
        expand_capture(manifest(compressed, 256), compressed, maximum_bytes=1024)
    with pytest.raises(ValueError, match="storage.*byte bounds"):
        expand_capture(manifest(compressed, len(raw)), compressed, maximum_bytes=1024)
    for invalid in (compressed + zlib.compress(b"hidden second stream"), compressed[:-1]):
        with pytest.raises(ValueError, match="expansion.*byte bounds"):
            expand_capture(manifest(invalid, len(raw)), invalid, maximum_bytes=len(raw))


@pytest.mark.parametrize("limit", ["MAX_ARCHIVED_HISTORY_RECORDS", "MAX_ARCHIVED_HISTORY_BYTES",
                                  "MAX_EXPANDED_HISTORY_BYTES", "MAX_CAPTURE_BYTES"])
def test_archive_overflow_refuses_before_fetching_compressed_or_source_payloads(archived, monkeypatch, limit):
    _, registry, collector, declared = archived
    captured = collector.capture(declared.trial_id)
    import trade_graph.runtime_evidence as module

    monkeypatch.setattr(module, limit, 0)
    statements = []
    registry.connection.set_trace_callback(statements.append)
    with pytest.raises(ValueError, match="verification.*bounds"):
        collector.verify(captured)
    assert not any("SELECT compressed_data" in statement for statement in statements)
    assert len(registry._rows("runtime_capture")) == 1


def test_expanded_metadata_budget_rejects_collecting_another_capture_and_preserves_all_history(archived):
    runtime, registry, collector, declared = archived
    latest = collector.capture(declared.trial_id)
    # Corruption simulates an understated/unusable retained manifest; the
    # protected aggregate preflight refuses before fetching compressed bytes.
    registry.connection.execute("DROP TRIGGER evaluation_no_update")
    row = registry._rows("runtime_capture")[0]
    manifest = json.loads(row["document_json"])
    manifest["expanded_bytes"] = 2 * 1024 * 1024 * 1024
    raw = json.dumps(manifest, separators=(",", ":"))
    registry.connection.execute("UPDATE evaluation_records SET document_json=?,document_sha256=? "
                                "WHERE kind='runtime_capture' AND record_key=?",
                                (raw, document_hash(raw), row["record_key"]))
    statements = []
    registry.connection.set_trace_callback(statements.append)
    runtime.clock.advance(1)
    with pytest.raises(ValueError, match="verification.*bounds"):
        collector.capture(declared.trial_id)
    assert not any("SELECT compressed_data" in statement for statement in statements)
    assert len(registry._rows("runtime_capture")) == 1
    assert latest.evaluation_snapshot.report_json


def test_snapshot_verification_reads_only_exact_handoff_without_loading_prior_reports(archived, monkeypatch):
    runtime, registry, collector, declared = archived
    collector.capture(declared.trial_id)
    runtime.clock.advance(1)
    current = collector.capture(declared.trial_id)
    original = registry._rows

    def reads(kind, *args, **kwargs):
        if kind == "snapshot":
            raise AssertionError("all historical snapshot bodies must not be fetched")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(registry, "_rows", reads)
    registry.verify_snapshot(current.evaluation_snapshot)
    assert collector.verify(current).database_consistent is True


@pytest.mark.parametrize("limit,ceiling", [
    ("MAX_ARCHIVED_HISTORY_RECORDS", 3),
    ("MAX_ARCHIVED_HISTORY_BYTES", 100000),
    ("MAX_EXPANDED_HISTORY_BYTES", 100000),
])
def test_honest_collection_stops_at_the_predeclared_aggregate_budget_without_pruning(
    tmp_path, monkeypatch, limit, ceiling,
):
    import trade_graph.runtime_evidence as module

    # This trusted test installation selects a lower bound before binding. There
    # is no runtime method for increasing or rewriting a declared policy.
    monkeypatch.setattr(module, limit, ceiling)
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    try:
        registry.register(declared)
        collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock,
                                             deployment_id="fixture", history_storage="lossless_zlib")
        collector.bind(declared.trial_id)
        retained = []
        for _ in range(50):
            runtime.clock.advance(1)
            try:
                capture = collector.capture(declared.trial_id)
            except ValueError as error:
                assert "verification" in str(error) and "bounds" in str(error)
                break
            retained.append(capture)
        else:
            pytest.fail("the finite preregistered aggregate bound must stop collection")
        assert retained
        assert len(registry._rows("runtime_capture")) == len(retained)
        assert len(registry._rows("snapshot")) == len(retained)
        assert registry.connection.execute("SELECT COUNT(*) FROM runtime_capture_blobs").fetchone()[0] == len(retained)
        assert collector.verify(retained[-1]).database_consistent is True
        for capture in retained:
            key = document_hash(capture.model_dump_json())
            assert collector._retained_capture(key, declared.trial_id)["document_json"] == capture.model_dump_json()
        # Increasing the protected implementation ceiling leaves the lower
        # already-retained policy intact; the same next capture still fails.
        monkeypatch.setattr(module, limit, ceiling * 100)
        with pytest.raises(ValueError, match="verification.*bounds"):
            collector.capture(declared.trial_id)
        assert len(registry._rows("runtime_capture")) == len(retained)
        assert len(registry._rows("snapshot")) == len(retained)
    finally:
        registry.close()
        runtime.database.close()


@pytest.mark.parametrize("kind", ["runtime_binding", "runtime_capture"])
def test_historical_trial_metadata_cannot_move_retained_financial_facts_to_another_trial(archived, kind):
    runtime, registry, collector, declared = archived
    collector.capture(declared.trial_id)
    runtime.clock.advance(1)
    current = collector.capture(declared.trial_id)
    row = registry._rows(kind)[0]
    registry.connection.execute("DROP TRIGGER evaluation_no_update")
    registry.connection.execute("UPDATE evaluation_records SET trial_id='foreign-trial' WHERE kind=? AND record_key=?",
                                (kind, row["record_key"]))
    with pytest.raises(ValueError, match="stale|history.*identity|bound before|binding"):
        collector.verify(current)


def test_standalone_snapshot_emission_obeys_the_same_frozen_aggregate_archive_budget(tmp_path, monkeypatch):
    import trade_graph.runtime_evidence as module

    monkeypatch.setattr(module, "MAX_ARCHIVED_HISTORY_BYTES", 100000)
    runtime = _stack(tmp_path)
    runtime.clock.advance((REGISTERED - runtime.clock.now()).total_seconds())
    declared = protocol(count=2, portfolio_id=runtime.portfolio_id, capital_eur="100")
    registry = TrialRegistry(tmp_path / "trial.sqlite", runtime.clock)
    try:
        registry.register(declared)
        collector = RuntimeEvidenceCollector(runtime.database, registry, runtime.clock,
                                             deployment_id="fixture", history_storage="lossless_zlib")
        collector.bind(declared.trial_id)
        retained = []
        for _ in range(50):
            runtime.clock.advance(1)
            try:
                snapshot = registry.snapshot(declared.trial_id)
            except ValueError as error:
                assert "verification" in str(error) and "bounds" in str(error)
                break
            retained.append(snapshot)
        else:
            pytest.fail("snapshot documents must consume the frozen aggregate storage budget")
        assert retained
        assert len(registry._rows("snapshot")) == len(retained)
        assert registry._rows("runtime_capture") == []
        assert registry.connection.execute("SELECT COUNT(*) FROM runtime_capture_blobs").fetchone()[0] == 0
        registry.verify_snapshot(retained[-1])
        with pytest.raises(ValueError, match="verification.*bounds"):
            collector.capture(declared.trial_id)
        assert len(registry._rows("snapshot")) == len(retained)
        assert registry._rows("runtime_capture") == []
    finally:
        registry.close()
        runtime.database.close()
