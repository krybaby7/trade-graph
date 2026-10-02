"""Real sealed-worker execution and independent offline shadow comparisons."""

import copy
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.integration.test_plugin_staging import GOOD, KEY, prepared

from trade_graph.adapters.engineering import plugin_runtime
from trade_graph.adapters.engineering.plugin_artifacts import sha256
from trade_graph.adapters.engineering.plugin_replay import ReplayPolicy
from trade_graph.adapters.engineering.plugin_runtime import (
    PluginRuntimeBuilder,
    runtime_builder_sha256,
    runtime_environment_sha256,
    strict_document,
)
from trade_graph.adapters.engineering.plugin_shadow import (
    NumericEvidence,
    PluginShadowRunner,
    ShadowCase,
    ShadowCorpus,
    ShadowPolicy,
    load_corpus,
    retain_corpus,
    shadow_runner_sha256,
)
from trade_graph.kernel.process_boundary import BoundaryPolicy

NOW = datetime(2026, 10, 2, 10, tzinfo=UTC)
IMPROVED = """
def propose(snapshot):
    return {'signal':'0' if snapshot['observations']['price_eur'] == '100' else '0.5'}
"""


@pytest.fixture(autouse=True)
def restore_synthetic_directory_modes(tmp_path):
    yield
    for directory, _, _ in os.walk(tmp_path, followlinks=False):
        Path(directory).chmod(0o700)


def stack(tmp_path, *, candidate_source=IMPROVED, baseline_release="baseline-1", wall="2"):
    replay_policy = ReplayPolicy(boundary=BoundaryPolicy(wall_seconds=Decimal(wall)))
    store, replay, protected, corpus_pin = prepared(tmp_path, policy=replay_policy)
    builder = PluginRuntimeBuilder(store, replay_validator=replay, expected_builder_sha256=runtime_builder_sha256(),
                                   expected_environment_sha256=runtime_environment_sha256(),
                                   expected_protected_manifest_sha256=protected, receipt_key=KEY)

    def build(source, release_id, previous_source):
        staged = store.stage(source, baseline_release_id=release_id, baseline_source_sha256=previous_source,
                             protected_manifest_sha256=protected, validation_corpus_sha256=corpus_pin)
        receipt = replay.validate(staged.manifest.build_digest)
        return builder.build(staged.manifest.build_digest, replay_receipt_sha256=receipt)

    baseline = build(GOOD, "initial-seed", "0" * 64)
    candidate = build(candidate_source, baseline_release, sha256(GOOD.encode()))
    return store, builder, protected, baseline, candidate


def point(value, *, decision=NOW, available=None, event=None):
    available = available or decision - timedelta(seconds=1)
    event = event or available - timedelta(seconds=1)
    return NumericEvidence(value=value, event_at_utc=event, available_at_utc=available,
                           source_ref="synthetic-point-source", source_sha256=sha256(value.encode()))


def comparison(store, builder, protected, baseline, *, policy_changes=None, cases=None):
    cases = cases or (ShadowCase(case_id="held-out-1", decision_at_utc=NOW,
                                evidence={"price_eur": point("200"), "spread_bps": point("3"),
                                          "position_quantity": point("0")}, expected_features={"signal": "0.5"}),)
    corpus = ShadowCorpus(corpus_id="synthetic-shadow", baseline_release_id="baseline-1",
                          baseline_runtime_build_sha256=baseline["runtime_build_sha256"],
                          baseline_source_sha256=baseline["source_sha256"], protected_manifest_sha256=protected,
                          cases=cases)
    retained = retain_corpus(store, corpus)
    fields = {"corpus_sha256": retained, "baseline_release_id": "baseline-1",
              "baseline_runtime_build_sha256": baseline["runtime_build_sha256"],
              "baseline_source_sha256": baseline["source_sha256"], "protected_manifest_sha256": protected,
              **(policy_changes or {})}
    policy = ShadowPolicy(**fields)
    runner = PluginShadowRunner(builder, policy=policy, expected_policy_sha256=policy.sha256,
                                expected_runner_sha256=shadow_runner_sha256(), receipt_key=KEY)
    return runner, corpus, retained, policy


def test_sealed_runtime_build_executes_its_actual_copied_worker_and_verifies_receipt(tmp_path, monkeypatch):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    manifest, source = builder.load(candidate["runtime_build_sha256"])
    assert source == IMPROVED and manifest.source_sha256 == sha256(IMPROVED.encode())
    assert manifest.baseline_source_sha256 == baseline["source_sha256"]
    assert builder.verify_build_receipt(candidate["build_receipt_sha256"],
                                        stage_build_digest=candidate["stage_build_digest"])["status"] == "built"
    actual_run = plugin_runtime.run_bounded
    commands = []

    def observe_command(command, *args, **kwargs):
        commands.append(command)
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(plugin_runtime, "run_bounded", observe_command)
    result = builder.evaluate(candidate["runtime_build_sha256"],
                              {"price_eur": "200", "spread_bps": "3", "position_quantity": "0"})
    assert result["status"] == "validated_numeric_proposal" and result["features"] == {"signal": "0.5"}
    path = store.root / "runtimes" / candidate["runtime_build_sha256"]
    assert commands[0][-1] == str(path / "worker.py")
    assert "-I" in commands[0] and "-S" in commands[0] and "-B" in commands[0]
    assert f"pycache_prefix={path / 'absent-cache'}" in commands[0]
    assert not (path / "absent-cache").exists()
    assert result["production_authorization"] is False and result["live_authorization"] is False
    assert protected == manifest.protected_manifest_sha256


@pytest.mark.parametrize("filename", ["source.py", "worker.py", "sandbox.py", "manifest.json"])
def test_tampered_bundle_cannot_run_or_transfer_its_prior_build_receipt(tmp_path, filename):
    store, builder, _, _, candidate = stack(tmp_path)
    path = store.root / "runtimes" / candidate["runtime_build_sha256"] / filename
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    path.chmod(0o400)
    with pytest.raises((ValueError, PermissionError)):
        builder.evaluate(candidate["runtime_build_sha256"],
                         {"price_eur": "200", "spread_bps": "3", "position_quantity": "0"})
    with pytest.raises((ValueError, PermissionError)):
        builder.verify_build_receipt(candidate["build_receipt_sha256"],
                                     stage_build_digest=candidate["stage_build_digest"])


def test_negative_replay_build_receipt_is_retained_without_any_runtime_bundle(tmp_path):
    store, builder, _, _, candidate = stack(tmp_path, candidate_source=GOOD.replace("'0'", "'1'"))
    assert candidate["status"] == "rejected" and candidate["runtime_build_sha256"] is None
    assert candidate["failures"] == ["independent_replay_rejected"]
    assert builder.verify_build_receipt(candidate["build_receipt_sha256"],
                                        stage_build_digest=candidate["stage_build_digest"])["status"] == "rejected"
    assert store.receipt(candidate["build_receipt_sha256"])["report"]["production_authorization"] is False


def test_shadow_executes_actual_baseline_and_candidate_with_point_in_time_version_bindings(tmp_path):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    runner, corpus, retained, policy = comparison(store, builder, protected, baseline)
    receipt = runner.compare(candidate["runtime_build_sha256"])
    report = runner.verify(receipt, candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "functional_shadow_passed" and report["failures"] == []
    assert [attempt["arm"] for attempt in report["attempts"]] == ["baseline", "candidate", "candidate", "baseline"]
    assert report["metrics"]["baseline"]["mean_absolute_error_upper"] == "0.5"
    assert report["metrics"]["candidate"]["mean_absolute_error_upper"] == "0"
    assert report["corpus_sha256"] == retained and report["shadow_policy_sha256"] == policy.sha256
    assert report["baseline_source_sha256"] == sha256(GOOD.encode())
    assert report["candidate_source_sha256"] == sha256(IMPROVED.encode())
    assert report["source_verification"] == "retained_declared_point_in_time"
    assert report["production_authorization"] is False and report["economic_evidence"] == "not_evaluated"
    assert KEY.decode() not in str(report)
    # Frozen nested caller mappings cannot rewrite the retained corpus.
    corpus.cases[0].expected_features["signal"] = "1"
    assert load_corpus(store, retained).cases[0].expected_features == {"signal": "0.5"}
    restarted = PluginShadowRunner(builder, policy=policy, expected_policy_sha256=policy.sha256,
                                   expected_runner_sha256=shadow_runner_sha256(), receipt_key=KEY)
    assert restarted.verify(receipt, candidate_digest=candidate["runtime_build_sha256"]) == report
    assert runner.compare(candidate["runtime_build_sha256"]) != receipt


@pytest.mark.parametrize("changes,failure", [
    ({"baseline_release": "fake-baseline"}, "actual_baseline_or_candidate_version_mismatch"),
    ({"state_schema": "different-state/v2"}, "runtime_contract_or_state_compatibility_mismatch"),
    ({"maximum_seconds": 1}, "shadow_resource_envelope_exceeded"),
])
def test_incompatible_or_unbounded_shadow_is_retained_negative_before_children_start(tmp_path, changes, failure):
    baseline_release = changes.get("baseline_release", "baseline-1")
    store, builder, protected, baseline, candidate = stack(tmp_path, baseline_release=baseline_release)
    policy_changes = {key: value for key, value in changes.items() if key != "baseline_release"}
    runner, _, _, _ = comparison(store, builder, protected, baseline, policy_changes=policy_changes)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "rejected" and failure in report["failures"]
    assert report["attempts"] == []


def test_shadow_functional_regression_is_negative_even_when_finite_replay_passed(tmp_path):
    store, builder, protected, baseline, candidate = stack(tmp_path, candidate_source=GOOD)
    assert candidate["status"] == "built"
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "rejected"
    assert report["failures"] == ["predeclared_functional_quality_or_regression_limit"]


def test_candidate_failure_in_shadow_has_independent_zero_fallback_and_retained_negative_attempts(tmp_path):
    source = """
def propose(snapshot):
    if snapshot['observations']['price_eur'] == '100':
        return {'signal':'0'}
    while True: pass
"""
    store, builder, protected, baseline, candidate = stack(tmp_path, candidate_source=source, wall="0.5")
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "rejected"
    assert "candidate_capability_resource_or_controller_failure" in report["failures"]
    failed = [attempt for attempt in report["attempts"] if attempt["arm"] == "candidate"]
    assert len(failed) == 2 and all(attempt["fallback"]["features"] == {"signal": "0"} for attempt in failed)
    assert all(attempt["fallback"]["status"] == "mutable_unavailable" for attempt in failed)


@pytest.mark.parametrize("fault", ["future_availability", "future_event", "naive", "non_utc", "duplicate_case"])
def test_invalid_point_in_time_clocks_and_case_identity_are_refused(tmp_path, fault):
    if fault == "future_availability":
        with pytest.raises(ValidationError, match="future source"):
            ShadowCase(case_id="c", decision_at_utc=NOW,
                       evidence={"price_eur": point("100", available=NOW + timedelta(seconds=1))},
                       expected_features={"signal": "0"})
    elif fault == "future_event":
        with pytest.raises(ValidationError, match="event time"):
            point("100", event=NOW, available=NOW - timedelta(seconds=1))
    elif fault in {"naive", "non_utc"}:
        changed = NOW.replace(tzinfo=None) if fault == "naive" else NOW.astimezone(timezone(timedelta(hours=1)))
        with pytest.raises(ValidationError, match="UTC"):
            point("100", event=changed)
    else:
        case = ShadowCase(case_id="c", decision_at_utc=NOW, evidence={"price_eur": point("100")},
                          expected_features={"signal": "0"})
        with pytest.raises(ValidationError, match="unique strictly chronological"):
            ShadowCorpus(corpus_id="c", baseline_release_id="b", baseline_runtime_build_sha256="0" * 64,
                         baseline_source_sha256="0" * 64, protected_manifest_sha256="0" * 64, cases=(case, case))


def test_corpus_and_receipt_tampering_and_candidate_substitution_fail_verification(tmp_path):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    runner, _, retained, _ = comparison(store, builder, protected, baseline)
    receipt = runner.compare(candidate["runtime_build_sha256"])
    forged = copy.deepcopy(store.receipt(receipt))
    forged["report"]["production_authorization"] = True
    with pytest.raises(PermissionError, match="authentication"):
        runner.verify(store.retain_receipt(forged), candidate_digest=candidate["runtime_build_sha256"])
    with pytest.raises(PermissionError, match="baseline/build/corpus"):
        runner.verify(receipt, candidate_digest=baseline["runtime_build_sha256"])
    path = store.root / "corpora" / retained / "corpus.json"
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    path.chmod(0o400)
    with pytest.raises(PermissionError, match="retained byte"):
        runner.verify(receipt, candidate_digest=candidate["runtime_build_sha256"])


@pytest.mark.parametrize("data", [b'{"x":1,"x":2}', b'{"x":0.1}', b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'])
def test_duplicate_deep_or_binary_float_json_is_refused_without_unbounded_parent_work(data):
    with pytest.raises(ValueError):
        strict_document(data)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0e-999999999", "invalid", "0.00"])
def test_comparison_policy_rejects_unbounded_or_noncanonical_decimal_tolerances(value):
    with pytest.raises((ValidationError, ValueError)):
        ShadowPolicy(corpus_sha256="0" * 64, baseline_release_id="b", baseline_runtime_build_sha256="0" * 64,
                     baseline_source_sha256="0" * 64, protected_manifest_sha256="0" * 64,
                     maximum_mean_absolute_error=value)


def test_exact_regression_rejects_a_difference_hidden_by_rounded_display_means(tmp_path):
    source = """
def propose(snapshot):
    return {'signal':'0.000000000000000001' if snapshot['observations']['price_eur'] == '300' else '0'}
"""
    store, builder, protected, baseline, candidate = stack(tmp_path, candidate_source=source)
    cases = tuple(ShadowCase(case_id=f"point-{index}", decision_at_utc=NOW + timedelta(seconds=index),
        evidence={"price_eur": point(price, decision=NOW + timedelta(seconds=index)),
                  "spread_bps": point("3"), "position_quantity": point("0")},
        expected_features={"signal": "1" if index == 0 else "0"})
        for index, price in enumerate(("200", "300", "400")))
    runner, _, _, _ = comparison(store, builder, protected, baseline, cases=cases,
        policy_changes={"maximum_mean_absolute_error": "1", "maximum_absolute_error": "1"})
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["metrics"]["candidate"]["mean_absolute_error_upper"] == report["metrics"]["baseline"][
        "mean_absolute_error_upper"]
    assert report["status"] == "rejected"
    assert report["failures"] == ["predeclared_functional_quality_or_regression_limit"]


def test_copied_worker_denies_file_network_process_and_key_access_on_actual_host(tmp_path, monkeypatch):
    sentinel = tmp_path / "synthetic-protected-file"
    sentinel.write_text("protected offline state")
    sentinel.chmod(0o666)
    monkeypatch.setenv("SYNTHETIC_OWNER_BUNDLE_KEY", KEY.decode())
    source = f"""
import os, socket
def propose(snapshot):
    if snapshot['observations']['price_eur'] == '100':
        return {{'signal':'0'}}
    assert 'SYNTHETIC_OWNER_BUNDLE_KEY' not in os.environ
    assert 'expected_features' not in snapshot
    for call in (lambda: os.open({str(sentinel)!r},os.O_RDWR),
                 lambda: socket.socket(socket.AF_INET,socket.SOCK_STREAM), lambda: os.fork()):
        try:
            call()
            return {{'signal':'1'}}
        except OSError as error:
            assert error.errno == 1
    return {{'signal':'0.5'}}
"""
    store, builder, protected, baseline, candidate = stack(tmp_path, candidate_source=source)
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "functional_shadow_passed", report
    assert sentinel.read_text() == "protected offline state"
    assert KEY.decode() not in str(report)


def test_shadow_detects_nondeterminism_outside_the_development_replay_corpus(tmp_path):
    source = """
import os, decimal
def propose(snapshot):
    if snapshot['observations']['price_eur'] == '100':
        return {'signal':'0'}
    return {'signal':str(decimal.Decimal(os.getpid()) * decimal.Decimal('0.000000000000000001'))}
"""
    store, builder, protected, baseline, candidate = stack(tmp_path, candidate_source=source)
    assert candidate["status"] == "built"
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "rejected" and "candidate_nondeterministic_shadow" in report["failures"]
    features = [attempt["features"]["signal"] for attempt in report["attempts"] if attempt["arm"] == "candidate"]
    assert len(set(features)) == 2


def test_controller_errors_are_recorded_negatively_and_do_not_erase_baseline_attempts(tmp_path, monkeypatch):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    actual_evaluate = builder.evaluate

    def controller_error(digest, observations):
        if digest == candidate["runtime_build_sha256"]:
            raise ValueError("synthetic controller fault whose text is not copied")
        return actual_evaluate(digest, observations)

    monkeypatch.setattr(builder, "evaluate", controller_error)
    report = runner.verify(runner.compare(candidate["runtime_build_sha256"]),
                           candidate_digest=candidate["runtime_build_sha256"])
    assert report["status"] == "rejected"
    assert "candidate_capability_resource_or_controller_failure" in report["failures"]
    assert [attempt["status"] for attempt in report["attempts"]] == [
        "validated_numeric_proposal", "controller_rejected", "controller_rejected", "validated_numeric_proposal"]
    assert "whose text is not copied" not in str(report)


def test_changed_copied_worker_during_shadow_retains_negative_attempts_and_old_evidence(tmp_path, monkeypatch):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    runner, _, _, _ = comparison(store, builder, protected, baseline)
    actual_evaluate = builder.evaluate
    path = store.root / "runtimes" / candidate["runtime_build_sha256"] / "worker.py"
    original = path.read_bytes()

    def tamper_after_actual_candidate(digest, observations):
        result = actual_evaluate(digest, observations)
        if digest == candidate["runtime_build_sha256"]:
            path.chmod(0o600)
            path.write_bytes(original + b" ")
            path.chmod(0o400)
        return result

    monkeypatch.setattr(builder, "evaluate", tamper_after_actual_candidate)
    receipt = runner.compare(candidate["runtime_build_sha256"])
    retained = store.receipt(receipt)["report"]
    assert retained["status"] == "rejected" and len(retained["attempts"]) == 4
    assert "retained_artifact_changed_during_shadow" in retained["failures"]
    with pytest.raises(PermissionError, match="content mismatch"):
        runner.verify(receipt, candidate_digest=candidate["runtime_build_sha256"])
    path.chmod(0o600)
    path.write_bytes(original)
    path.chmod(0o400)
    assert runner.verify(receipt, candidate_digest=candidate["runtime_build_sha256"])["status"] == "rejected"


def test_restart_verification_requires_independent_owner_pins_and_exact_build_scope(tmp_path):
    store, builder, protected, baseline, candidate = stack(tmp_path)
    runner, _, _, policy = comparison(store, builder, protected, baseline)
    receipt = runner.compare(candidate["runtime_build_sha256"])
    for field in ("expected_policy_sha256", "expected_runner_sha256"):
        fields = {"expected_policy_sha256": policy.sha256, "expected_runner_sha256": shadow_runner_sha256()}
        fields[field] = "0" * 64
        with pytest.raises(PermissionError, match="owner-pinned"):
            PluginShadowRunner(builder, policy=policy, receipt_key=KEY, **fields)
    wrong_key = PluginShadowRunner(builder, policy=policy, expected_policy_sha256=policy.sha256,
                                  expected_runner_sha256=shadow_runner_sha256(),
                                  receipt_key=b"other-synthetic-key-41" * 2)
    with pytest.raises(PermissionError, match="authentication"):
        wrong_key.verify(receipt, candidate_digest=candidate["runtime_build_sha256"])
    with pytest.raises(PermissionError, match="scope/pin"):
        builder.verify_build_receipt(candidate["build_receipt_sha256"],
                                     stage_build_digest=baseline["stage_build_digest"])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0e-999999999", "1e999999999", "0.00", "invalid"])
def test_retained_numeric_evidence_rejects_nonfinite_expanding_and_noncanonical_values(value):
    with pytest.raises(ValidationError, match="canonical numeric"):
        point(value)
