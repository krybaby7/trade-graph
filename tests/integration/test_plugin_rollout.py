"""Offline automatic activation, actual confined effects and independent recovery."""

import json
import os
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from tests.integration.test_plugin_engineering import flow
from tests.integration.test_plugin_runtime_shadow import IMPROVED, NOW, point

from trade_graph.adapters.engineering.plugin_artifacts import sha256
from trade_graph.adapters.engineering.plugin_shadow import ShadowCase, load_corpus, retain_corpus
from trade_graph.application.plugin_rollout import (
    EXPERIMENT,
    KIND,
    OfflinePluginRolloutController,
    PluginRolloutPolicy,
    rollout_controller_sha256,
)
from trade_graph.domain.errors import AuthorityDenied, StaleState

KEY = b"synthetic-offline-rollout-private-key-32bytes-41"
INPUT = {"price_eur": "200", "spread_bps": "3", "position_quantity": "0"}
BAD_PRICE = """
def propose(snapshot):
    if snapshot['observations']['price_eur'] == '999':
        open('/etc/passwd').read()
    return {'signal':'0' if snapshot['observations']['price_eur'] == '100' else '0.5'}
"""


@pytest.fixture(autouse=True)
def restore_private_fixture_modes(tmp_path):
    yield
    for directory, _, _ in os.walk(tmp_path, followlinks=False):
        Path(directory).chmod(0o700)


def prepared(tmp_path, *, source=IMPROVED, price="200", expected="0.5", deadline=300):
    captured = {}

    def initialize(engineer, pid):
        # Only this fresh synthetic fixture receives the explicit offline marker.
        engineer.database.execute("UPDATE portfolios SET experiment_id = ? WHERE portfolio_id = ?", (EXPERIMENT, pid))
        original = load_corpus(engineer.store, json.loads(engineer.shadow._policy_bytes)["corpus_sha256"])
        case = ShadowCase(case_id="independent-health", decision_at_utc=NOW,
                          evidence={"price_eur": point(price), "spread_bps": point("3"),
                                    "position_quantity": point("0")}, expected_features={"signal": expected})
        corpus = original.model_copy(update={"corpus_id": "synthetic-independent-health", "cases": (case,)})
        digest = retain_corpus(engineer.store, corpus)
        policy = PluginRolloutPolicy(portfolio_id=pid, commission_policy_sha256=engineer.policy_pin,
                                     health_corpus_sha256=digest, observation_deadline_seconds=deadline)
        controller = OfflinePluginRolloutController(engineer, policy=policy, expected_policy_sha256=policy.sha256,
                                                    expected_controller_sha256=rollout_controller_sha256(),
                                                    receipt_key=KEY)
        controller.initialize_baseline()
        captured["controller"] = controller

    current = flow(tmp_path, source=source, initialize_baseline=initialize)
    current.rollout = captured["controller"]
    return current


def activate(current):
    assert current.run() == 1
    assert current.output()["_status"] == "SUCCEEDED", current.output()
    candidate = current.output()["candidate_id"]
    result = current.rollout.activate(candidate, expected_generation=0)
    assert result["state"] == "OBSERVING" and result["generation"] == 1
    return candidate


def restarted(current):
    old = current.rollout
    current.rollout = OfflinePluginRolloutController(
        current.engineer, policy=old.policy, expected_policy_sha256=old.policy_pin,
        expected_controller_sha256=old.controller_pin, receipt_key=KEY,
    )
    return current.rollout


def history(current):
    return {name: [dict(row) for row in current.db.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()]
            for name in ("ledger_events", "journal_postings", "usage_receipts", "budget_reservations",
                         "model_invocations", "fills", "order_attempts")}


def test_actual_numeric_behavior_changes_after_automatic_activation_and_survives_restart(tmp_path):
    current = prepared(tmp_path)
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}
    candidate = activate(current)
    initial_history = history(current)
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0.5"}
    assert current.rollout.health() == "ACTIVE"
    assert restarted(current).maintain() == "ACTIVE"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0.5"}
    active, release = current.rollout._active()
    assert active["version_id"] == candidate and active["generation"] == 1
    assert json.loads(active["fingerprint_json"])["kind"] == KIND
    assert release["production_authorization"] is release["live_authorization"] is False
    assert release["economic_evidence"] == "not_evaluated"
    assert len(current.receipts()) == 1
    assert history(current) == initial_history


def test_new_health_input_runs_actual_denied_source_and_rolls_back_without_a_model(tmp_path, monkeypatch):
    current = prepared(tmp_path, source=BAD_PRICE, price="999")
    candidate = activate(current)
    current.engineer.ledger.deposit(current.pid, "USD", Decimal("10000"), "synthetic-preserved-capital")
    current.engineer.ledger.add_expense(current.pid, expense_id="synthetic-preserved-expense",
        native_amount=Decimal("0.25"), native_currency="EUR", reporting_amount=Decimal("0.25"),
        reporting_currency="EUR", embedded=False, source="synthetic-retained-cost")
    initial_history = history(current)
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda _: pytest.fail("rollback used a model"))
    assert current.rollout.health() == "ROLLED_BACK"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}
    assert restarted(current).maintain() == "BASELINE"
    active, _ = current.rollout._active()
    assert active["version_id"] == "baseline-1" and active["generation"] == 2
    assert current.db.execute("SELECT state FROM candidates WHERE candidate_id = ?",
                              (candidate,)).fetchone()[0] == "ROLLED_BACK"
    assert history(current) == initial_history
    sample = current.db.execute("SELECT document_json FROM version_observations").fetchone()[0]
    receipt = json.loads(sample)["receipt_sha256"]
    report = current.engineer.store.receipt(receipt)["report"]
    assert report["phase"] == "COMPLETED" and report["healthy"] is False
    assert report["outcome"]["status"] == "rejected"
    assert report["outcome"]["fallback"]["status"] == "mutable_unavailable"


def test_functional_deadline_recovery_restores_durable_baseline_after_restart(tmp_path):
    current = prepared(tmp_path, deadline=10)
    activate(current)
    initial_history = history(current)
    current.clock.advance(11)
    assert restarted(current).maintain() == "ROLLED_BACK"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}
    assert history(current) == initial_history


def test_tampered_active_bundle_can_rollback_to_independently_valid_previous_bytes(tmp_path):
    current = prepared(tmp_path)
    activate(current)
    active, release = current.rollout._active()
    source = current.engineer.store.root / "runtimes" / release["runtime_build_sha256"] / "source.py"
    source.chmod(0o600)
    source.write_text("def propose(snapshot): return {'signal':'1'}\n")
    source.chmod(0o400)
    initial_history = history(current)
    assert current.rollout.maintain() == "ROLLED_BACK"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}
    assert current.rollout._active()[0]["generation"] == active["generation"] + 1
    assert history(current) == initial_history


def test_cas_quiescence_and_explicit_offline_fence_prevent_activation_on_other_state(tmp_path):
    current = prepared(tmp_path)
    assert current.run() == 1
    candidate = current.output()["candidate_id"]
    with pytest.raises(StaleState, match="generation changed"):
        current.rollout.activate(candidate, expected_generation=99)
    task = current.office.scheduler.add_task(role="trader", objective="synthetic in-flight", portfolio_id=current.pid)
    lease = current.office.scheduler.claim("fixture-worker", roles={"trader"})
    assert lease is not None
    with pytest.raises(StaleState, match="not quiescent"):
        current.rollout.activate(candidate, expected_generation=0)
    current.office.scheduler.finish(lease, {}, "SUCCEEDED")
    assert current.db.execute("SELECT status FROM tasks WHERE task_id = ?", (task,)).fetchone()[0] == "SUCCEEDED"
    current.db.execute("UPDATE portfolios SET experiment_id = 'ordinary-paper' WHERE portfolio_id = ?", (current.pid,))
    with pytest.raises(AuthorityDenied, match="explicit isolated"):
        current.rollout.activate(candidate, expected_generation=0)


def test_r1_shape_real_provider_and_receipt_states_are_refused(tmp_path):
    current = prepared(tmp_path)
    assert current.run() == 1
    candidate = current.output()["candidate_id"]
    active = current.db.execute("SELECT fingerprint_json FROM active_versions WHERE portfolio_id = ?",
                                (current.pid,)).fetchone()[0]
    current.db.execute("UPDATE active_versions SET fingerprint_json = ? WHERE portfolio_id = ?",
                       (json.dumps({"artifact": current.baseline}), current.pid))
    with pytest.raises(AuthorityDenied, match="R1/trading"):
        current.rollout.activate(candidate, expected_generation=0)
    current.db.execute("UPDATE active_versions SET fingerprint_json = ? WHERE portfolio_id = ?", (active, current.pid))
    leader = current.db.execute("SELECT r.receipt_id FROM usage_receipts r JOIN budget_reservations b "
                                "ON b.reservation_id = r.reservation_id WHERE b.role = 'leader'").fetchone()
    assert leader is not None
    current.db.execute("UPDATE usage_receipts SET synthetic = 0 WHERE receipt_id = ?", (leader[0],))
    with pytest.raises(AuthorityDenied, match="real usage receipts"):
        current.rollout.activate(candidate, expected_generation=0)
    current.db.execute("UPDATE usage_receipts SET synthetic = 1")
    current.db.execute("UPDATE usage_receipts SET synthetic = 0")
    with pytest.raises(AuthorityDenied, match="real usage receipts"):
        current.rollout.activate(candidate, expected_generation=0)
    current.db.execute("UPDATE usage_receipts SET synthetic = 1")
    request = current.db.execute("SELECT request_json FROM model_invocations WHERE task_id = ?",
                                 (current.tid,)).fetchone()[0]
    replaced = json.loads(request)
    replaced["provider"] = "openai"
    current.db.execute("UPDATE model_invocations SET request_json = ? WHERE task_id = ?",
                       (json.dumps(replaced), current.tid))
    with pytest.raises(AuthorityDenied, match="real provider"):
        current.rollout.activate(candidate, expected_generation=0)


def test_corrupt_pointer_event_and_wrong_key_fail_closed_without_financial_changes(tmp_path):
    current = prepared(tmp_path)
    activate(current)
    initial_history = history(current)
    original_key = current.rollout._key
    current.rollout._key = b"other-synthetic-rollout-receipt-key-32bytes"
    with pytest.raises(PermissionError, match="authentication"):
        current.rollout.maintain()
    current.rollout._key = original_key
    event = current.db.execute(
        "SELECT event_id, details_json FROM version_events ORDER BY rowid DESC LIMIT 1",
    ).fetchone()
    current.db.execute("UPDATE version_events SET details_json = ? WHERE event_id = ?",
                       (json.dumps({"receipt_sha256": "f" * 64}), event["event_id"]))
    with pytest.raises(OSError):
        current.rollout.maintain()
    current.db.execute("UPDATE version_events SET details_json = ? WHERE event_id = ?",
                       (event["details_json"], event["event_id"]))
    current.db.execute("UPDATE active_versions SET generation = 7 WHERE portfolio_id = ?", (current.pid,))
    with pytest.raises(AuthorityDenied, match="durable generation"):
        current.rollout.maintain()
    assert history(current) == initial_history


def test_persisted_active_flag_without_independent_samples_triggers_rollback(tmp_path):
    current = prepared(tmp_path)
    activate(current)
    current.db.execute("UPDATE version_rollouts SET state = 'ACTIVE' WHERE portfolio_id = ?", (current.pid,))
    assert restarted(current).maintain() == "ROLLED_BACK"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}


def test_health_intent_is_durable_before_child_and_interrupted_work_is_not_replayed(tmp_path, monkeypatch):
    current = prepared(tmp_path)
    activate(current)

    class Crash(BaseException):
        pass

    def interrupt(digest, inputs):
        row = current.db.execute("SELECT document_json FROM version_observations").fetchone()
        assert row is not None
        report = current.engineer.store.receipt(json.loads(row[0])["receipt_sha256"])["report"]
        assert report["phase"] == "STARTED" and report["outcome"] is None
        assert report["snapshot_sha256"] == sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode())
        raise Crash

    monkeypatch.setattr(current.engineer.builder, "evaluate", interrupt)
    with pytest.raises(Crash):
        current.rollout.health()
    initial_history = history(current)
    monkeypatch.setattr(current.engineer.builder, "evaluate", lambda *a: pytest.fail("interrupted child was replayed"))
    assert restarted(current).maintain() == "ROLLED_BACK"
    assert history(current) == initial_history


def test_health_comparison_ignores_ambient_decimal_precision_and_rolls_back_exact_regression(tmp_path):
    current = prepared(tmp_path, expected="-0.00001")
    activate(current)
    with localcontext() as context:
        context.prec = 3
        assert not current.rollout._numeric_outcome({"status": "validated_numeric_proposal",
                                                     "features": {"signal": "1.00001"}})
        assert current.rollout.health() == "ROLLED_BACK"
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}


def test_corrupt_previous_source_prevents_fictional_successful_rollback(tmp_path):
    current = prepared(tmp_path, deadline=1)
    activate(current)
    baseline = (current.engineer.store.root / "runtimes" /
                current.engineer.policy.baseline_runtime_build_sha256 / "source.py")
    baseline.chmod(0o600)
    baseline.write_text("def propose(snapshot): return {'signal':'1'}\n")
    baseline.chmod(0o400)
    current.clock.advance(2)
    with pytest.raises((ValueError, OSError)):
        current.rollout.maintain()
    assert current.db.execute("SELECT generation FROM active_versions WHERE portfolio_id = ?",
                              (current.pid,)).fetchone()[0] == 1


def test_fixed_advance_consumes_only_actual_commissioned_ready_candidate_and_health(tmp_path):
    current = prepared(tmp_path)
    assert current.rollout.advance()["state"] == "BASELINE"
    assert current.run() == 1
    result = current.rollout.advance()
    assert result["state"] == "ACTIVE" and result["generation"] == 1
    assert result["production_authorization"] is result["live_authorization"] is False
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0.5"}
    assert current.rollout.advance()["state"] == "ACTIVE"
    assert len(current.receipts()) == 1


def test_empty_feature_success_cannot_pass_health_or_hide_resource_failure(tmp_path, monkeypatch):
    current = prepared(tmp_path)
    activate(current)
    original = current.engineer.builder.evaluate
    monkeypatch.setattr(current.engineer.builder, "evaluate",
                        lambda *a: {"status": "validated_numeric_proposal", "features": {}})
    assert current.rollout.health() == "ROLLED_BACK"
    monkeypatch.setattr(current.engineer.builder, "evaluate", original)
    assert current.rollout.evaluate(INPUT)["features"] == {"signal": "0"}


def test_corrupt_extra_or_oversized_sample_history_is_refused_before_accepting_active(tmp_path):
    current = prepared(tmp_path)
    activate(current)
    assert current.rollout.health() == "ACTIVE"
    rollout = current.db.execute("SELECT rollout_id FROM version_rollouts WHERE portfolio_id = ?",
                                 (current.pid,)).fetchone()[0]
    sample = current.db.execute("SELECT document_json FROM version_observations").fetchone()[0]
    current.db.execute("INSERT INTO version_observations VALUES (?, ?, ?, ?)",
                       (rollout, "extra-case", sample, "2026-01-01T00:00:00Z"))
    with pytest.raises(AuthorityDenied, match="sample count"):
        current.rollout.maintain()
    current.db.execute("DELETE FROM version_observations WHERE observation_id = 'extra-case'")
    current.db.execute("UPDATE version_observations SET document_json = ?", (" " * 10000,))
    with pytest.raises(AuthorityDenied, match="pointer byte bound"):
        current.rollout.maintain()
