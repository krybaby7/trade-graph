"""Offline T22 preparation: immutable bytes and genuine confined finite replay.

All keys, baselines, credentials, observations and expected features are synthetic.
Passing these checks grants neither production Engineer nor live authority.
"""

import copy
import hashlib
import hmac
import json
import os
import stat
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.adapters.engineering.plugin_artifacts import (
    PluginStageManifest,
    PluginStageStore,
    canonical_bytes,
    sha256,
)
from trade_graph.adapters.engineering.plugin_replay import (
    PluginReplayValidator,
    ReplayCase,
    ReplayPolicy,
    corpus_sha256,
    protected_manifest_sha256,
    validator_fingerprint,
)
from trade_graph.kernel.process_boundary import BoundaryPolicy, protected_fingerprint

OBSERVATIONS = {"price_eur": "100", "spread_bps": "3", "position_quantity": "0"}
KEY = b"synthetic-offline-owner-receipt-key-41"
BASELINE = "a" * 64
GOOD = "def propose(snapshot):\n    return {'signal':'0'}\n"


@pytest.fixture(autouse=True)
def restore_synthetic_fixture_permissions(tmp_path):
    yield
    # Sealing is a tested runtime requirement. Restore only this test's temporary
    # directory modes so pytest can remove symlink/hardlink attack fixtures.
    for root, _, _ in os.walk(tmp_path, followlinks=False):
        Path(root).chmod(0o700)


def prepared(tmp_path, *, policy=None, cases=None, receipt_key=KEY):
    store = PluginStageStore(tmp_path / "private-plugin-store")
    policy = policy or ReplayPolicy()
    cases = cases or (ReplayCase(case_id="market-1", observations=dict(OBSERVATIONS),
                                expected_features={"signal": "0"}),)
    kernel_pin, validator_pin = protected_fingerprint(), validator_fingerprint()
    manifest_pin = protected_manifest_sha256(policy, kernel_sha256=kernel_pin, validator_sha256=validator_pin)
    corpus_pin = corpus_sha256(cases)
    validator = PluginReplayValidator(store, policy=policy, cases=cases, expected_kernel_sha256=kernel_pin,
                                      expected_validator_sha256=validator_pin,
                                      expected_protected_manifest_sha256=manifest_pin,
                                      expected_corpus_sha256=corpus_pin, receipt_key=receipt_key)
    return store, validator, manifest_pin, corpus_pin


def stage(store, source, manifest_pin, corpus_pin, *, baseline="baseline-1"):
    return store.stage(source, baseline_release_id=baseline, baseline_source_sha256=BASELINE,
                       protected_manifest_sha256=manifest_pin, validation_corpus_sha256=corpus_pin)


def test_staging_is_sealed_content_addressed_idempotent_and_baseline_bound(tmp_path):
    store, _, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    assert stage(store, GOOD, manifest_pin, corpus_pin) == candidate
    assert store.load(candidate.manifest.build_digest) == candidate
    assert candidate.manifest.source_sha256 == hashlib.sha256(GOOD.encode()).hexdigest()
    assert candidate.manifest_sha256 == sha256(canonical_bytes(candidate.manifest.model_dump()))
    path = store.root / "stages" / candidate.manifest.build_digest
    assert stat.S_IMODE(path.stat().st_mode) == 0o500
    assert {file.name: stat.S_IMODE(file.stat().st_mode) for file in path.iterdir()} == {
        "manifest.json": 0o400, "source.py": 0o400}
    assert candidate.manifest.build_digest != stage(store, GOOD, manifest_pin, corpus_pin,
                                                   baseline="baseline-2").manifest.build_digest
    with pytest.raises(ValidationError, match="frozen"):
        candidate.manifest.baseline_release_id = "other"
    with pytest.raises(ValueError, match="digest"):
        store.load("../outside")


def test_source_tampering_is_detected_even_after_restoring_sealed_modes(tmp_path):
    store, _, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    source = store.root / "stages" / candidate.manifest.build_digest / "source.py"
    source.chmod(0o600)
    source.write_text(GOOD.replace("'0'", "'1'"))
    source.chmod(0o400)
    with pytest.raises(ValueError, match="source content"):
        store.load(candidate.manifest.build_digest)
    with pytest.raises(ValueError, match="conflicts"):
        stage(store, GOOD, manifest_pin, corpus_pin)


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "extra_file", "writable_file", "writable_directory"])
def test_untrusted_stage_storage_shapes_are_refused(tmp_path, attack):
    store, _, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    directory = store.root / "stages" / candidate.manifest.build_digest
    source = directory / "source.py"
    if attack in {"symlink", "hardlink", "extra_file"}:
        directory.chmod(0o700)
        if attack == "symlink":
            source.unlink()
            target = tmp_path / "candidate.py"
            target.write_text(GOOD)
            source.symlink_to(target)
        elif attack == "hardlink":
            os.link(source, tmp_path / "shared.py")
        else:
            (directory / "candidate-passed.json").write_text('{"passed":true}')
        directory.chmod(0o500)
    elif attack == "writable_file":
        source.chmod(0o600)
    else:
        directory.chmod(0o700)
    with pytest.raises((PermissionError, OSError)):
        store.load(candidate.manifest.build_digest)


def test_private_roots_and_parent_symlinks_are_refused(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(OSError):
        PluginStageStore(link / "store")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(PermissionError, match="owner-private"):
        PluginStageStore(public)
    store = PluginStageStore(tmp_path / "store")
    store.root.chmod(0o755)
    with pytest.raises(PermissionError, match="owner-private"):
        store.stage(GOOD, baseline_release_id="b", baseline_source_sha256=BASELINE,
                    protected_manifest_sha256=BASELINE, validation_corpus_sha256=BASELINE)


def test_candidate_cannot_add_permissions_or_dependencies_to_manifest(tmp_path):
    store, _, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    document = candidate.manifest.model_dump()
    for field, value in (("passed", True), ("owner_grant", True), ("dependencies", ["attacker"]),
                         ("class_name", "selected_application_code"), ("schema_version", True)):
        with pytest.raises(ValidationError):
            PluginStageManifest.model_validate({**document, field: value})
    with pytest.raises(ValueError, match="byte bound"):
        stage(store, "x" * 65537, manifest_pin, corpus_pin)
    with pytest.raises(ValueError, match="UTF-8"):
        stage(store, {"passed": True}, manifest_pin, corpus_pin)


def test_finite_replay_retains_exact_parent_receipt_and_survives_verifier_restart(tmp_path):
    cases = (ReplayCase(case_id="first", observations=dict(OBSERVATIONS), expected_features={"signal": "0"}),
             ReplayCase(case_id="second", observations={**OBSERVATIONS, "price_eur": "200"},
                        expected_features={"signal": "0"}))
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path, cases=cases)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    receipt = validator.validate(candidate.manifest.build_digest)
    report = validator.verified_receipt(receipt, build_digest=candidate.manifest.build_digest)
    assert report["status"] == "finite_replay_passed" and report["failures"] == []
    assert [(a["case_id"], a["repetition"]) for a in report["attempts"]] == [
        ("first", 0), ("second", 0), ("second", 1), ("first", 1)]
    assert report["validation_receipt_sha256"] == receipt
    assert report["manifest_sha256"] == candidate.manifest_sha256
    assert report["production_authorization"] is False and report["live_authorization"] is False
    assert report["economic_evidence"] == "not_evaluated"
    assert KEY.decode() not in json.dumps(report)
    _, restarted, _, _ = prepared(tmp_path, cases=cases)
    assert restarted.verified_receipt(receipt, build_digest=candidate.manifest.build_digest) == report
    assert validator.validate(candidate.manifest.build_digest) != receipt  # All attempts retained, no overwrite.


def test_caller_mutation_of_nested_corpus_cannot_change_independent_expectations(tmp_path):
    case = ReplayCase(case_id="fixed", observations=dict(OBSERVATIONS), expected_features={"signal": "0"})
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path, cases=(case,))
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    case.expected_features["signal"] = "1"
    case.observations["credential"] = "synthetic-must-not-enter"
    report = validator.verified_receipt(validator.validate(candidate.manifest.build_digest),
                                        build_digest=candidate.manifest.build_digest)
    assert report["status"] == "finite_replay_passed"
    assert "synthetic-must-not-enter" not in json.dumps(report)


def test_new_process_identity_exposes_nondeterministic_replay_and_retains_failures(tmp_path):
    policy = ReplayPolicy(boundary=BoundaryPolicy(maximum_feature_magnitude=Decimal("1000000000000")))
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path, policy=policy)
    source = "import os\ndef propose(snapshot):\n    return {'signal':str(os.getpid())}\n"
    candidate = stage(store, source, manifest_pin, corpus_pin)
    report = validator.verified_receipt(validator.validate(candidate.manifest.build_digest),
                                        build_digest=candidate.manifest.build_digest)
    assert report["status"] == "rejected"
    assert report["failures"] == ["held_out_expectation_mismatch", "nondeterministic_replay"]
    assert len({a["features"]["signal"] for a in report["attempts"]}) == 2
    assert report["production_authorization"] is False


def test_candidate_can_neither_read_validation_key_nor_mutate_stage_on_actual_os_boundary(tmp_path, monkeypatch):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    monkeypatch.setenv("SYNTHETIC_OWNER_RECEIPT_KEY", KEY.decode())
    victim = tmp_path / "world-writable-sentinel"
    victim.write_text("protected synthetic evidence")
    victim.chmod(0o666)
    source = f"""
import os, socket
def propose(snapshot):
    assert 'SYNTHETIC_OWNER_RECEIPT_KEY' not in os.environ
    assert 'expected_features' not in snapshot and 'receipt_key' not in snapshot
    for action in [lambda: os.open({str(victim)!r}, os.O_RDWR),
                   lambda: os.open({str(store.root)!r}, os.O_RDONLY),
                   lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM),
                   lambda: os.fork()]:
        try:
            action()
            return {{'signal':'1'}}
        except OSError as error:
            assert error.errno == 1
    return {{'signal':'0'}}
"""
    candidate = stage(store, source, manifest_pin, corpus_pin)
    report = validator.verified_receipt(validator.validate(candidate.manifest.build_digest),
                                        build_digest=candidate.manifest.build_digest)
    assert report["status"] == "finite_replay_passed", report
    assert victim.read_text() == "protected synthetic evidence"
    assert KEY.decode() not in json.dumps(store.receipt(report["validation_receipt_sha256"]))


@pytest.mark.parametrize("source", [
    "def propose(snapshot):\n    while True: pass\n",
    "def propose(snapshot):\n    return {'signal':0.5}\n",
    "def propose(snapshot):\n    return {'signal':'0','enable_live':'1'}\n",
    "def propose(snapshot):\n    data=bytearray(256*1024*1024)\n    return {'signal':'0'}\n",
    "import os\ndef propose(snapshot):\n    while True: os.write(1,b'x'*8192)\n",
    "import os\ndef propose(snapshot):\n    os.write(1,b'{\"passed\":true,\"owner_grant\":true}')\n    os._exit(0)\n",
])
def test_resource_capability_and_output_failures_cannot_manufacture_passage(tmp_path, source):
    policy = ReplayPolicy(boundary=BoundaryPolicy(wall_seconds=Decimal("0.3")))
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path, policy=policy)
    candidate = stage(store, source, manifest_pin, corpus_pin)
    report = validator.verified_receipt(validator.validate(candidate.manifest.build_digest),
                                        build_digest=candidate.manifest.build_digest)
    assert report["status"] == "rejected"
    assert "capability_or_resource_or_contract_failure" in report["failures"]
    assert len(report["attempts"]) == 2
    assert report["production_authorization"] is False
    # Recovery/validation runs trusted code independently of the failed plugin.
    good = stage(store, GOOD, manifest_pin, corpus_pin)
    assert validator.verified_receipt(validator.validate(good.manifest.build_digest),
                                      build_digest=good.manifest.build_digest)["status"] == "finite_replay_passed"


def test_forged_copied_or_mutated_receipts_never_attest_another_candidate(tmp_path):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    receipt = validator.validate(candidate.manifest.build_digest)
    others = (stage(store, GOOD.replace("'0'", "'1'"), manifest_pin, corpus_pin),
              stage(store, GOOD, manifest_pin, corpus_pin, baseline="baseline-2"))
    for other in others:
        with pytest.raises(PermissionError, match="identity mismatch"):
            validator.verified_receipt(receipt, build_digest=other.manifest.build_digest)
    original = store.receipt(receipt)
    fake = copy.deepcopy(original)
    fake["report"]["production_authorization"] = True
    fake_digest = store.retain_receipt(fake)
    with pytest.raises(PermissionError, match="authentication"):
        validator.verified_receipt(fake_digest, build_digest=candidate.manifest.build_digest)
    fake["authentication_sha256"] = hmac.new(b"candidate-fake-key" * 4, canonical_bytes(fake["report"]),
                                               hashlib.sha256).hexdigest()
    with pytest.raises(PermissionError, match="authentication"):
        validator.verified_receipt(store.retain_receipt(fake), build_digest=candidate.manifest.build_digest)
    _, wrong_key, _, _ = prepared(tmp_path, receipt_key=b"another-synthetic-owner-key-123456789")
    with pytest.raises(PermissionError, match="authentication"):
        wrong_key.verified_receipt(receipt, build_digest=candidate.manifest.build_digest)


def test_stage_and_receipt_bind_independent_corpus_baseline_and_controller_pins(tmp_path):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    for changed in ({"manifest_pin": "0" * 64}, {"corpus_pin": "0" * 64}):
        candidate = stage(store, GOOD, changed.get("manifest_pin", manifest_pin),
                          changed.get("corpus_pin", corpus_pin))
        with pytest.raises(PermissionError, match="another validator"):
            validator.validate(candidate.manifest.build_digest)
    policy = ReplayPolicy()
    cases = (ReplayCase(case_id="fixed", observations=OBSERVATIONS, expected_features={"signal": "0"}),)
    args = {"policy": policy, "cases": cases, "expected_kernel_sha256": protected_fingerprint(),
            "expected_validator_sha256": validator_fingerprint(), "expected_protected_manifest_sha256": manifest_pin,
            "expected_corpus_sha256": corpus_sha256(cases), "receipt_key": KEY}
    for name in ("expected_kernel_sha256", "expected_validator_sha256", "expected_protected_manifest_sha256"):
        with pytest.raises(PermissionError, match="owner-pinned"):
            PluginReplayValidator(store, **{**args, name: "0" * 64})
    with pytest.raises(ValueError, match="corpus identity"):
        PluginReplayValidator(store, **{**args, "expected_corpus_sha256": "0" * 64})
    with pytest.raises(ValueError, match="authentication key"):
        PluginReplayValidator(store, **{**args, "receipt_key": b"too-short"})
    for changed in ({"repetitions": 1}, {"maximum_cases": 17}, {"maximum_validation_seconds": Decimal("61")}):
        with pytest.raises(ValueError, match="bounded"):
            replace(policy, **changed)


def test_private_receipt_tampering_is_detected_and_changed_stage_invalidates_old_receipt(tmp_path):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    receipt = validator.validate(candidate.manifest.build_digest)
    path = store.root / "receipts" / receipt / "receipt.json"
    envelope = store.receipt(receipt)
    path.chmod(0o600)
    path.write_bytes(canonical_bytes({**envelope, "authentication_sha256": sha256(b"attacker")}))
    path.chmod(0o400)
    with pytest.raises(ValueError, match="receipt content"):
        validator.verified_receipt(receipt, build_digest=candidate.manifest.build_digest)
    intact = validator.validate(candidate.manifest.build_digest)
    source = store.root / "stages" / candidate.manifest.build_digest / "source.py"
    source.chmod(0o600)
    source.write_text(GOOD.replace("'0'", "'1'"))
    source.chmod(0o400)
    with pytest.raises(ValueError, match="source content"):
        validator.verified_receipt(intact, build_digest=candidate.manifest.build_digest)


def test_parent_work_quota_retains_unrun_attempts_instead_of_starting_another_child(tmp_path):
    policy = ReplayPolicy(boundary=BoundaryPolicy(wall_seconds=Decimal("0.5")),
                          maximum_validation_seconds=Decimal("1"))
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path, policy=policy)
    source = "import time\ndef propose(snapshot):\n    time.sleep(30)\n    return {'signal':'0'}\n"
    candidate = stage(store, source, manifest_pin, corpus_pin)
    report = validator.verified_receipt(validator.validate(candidate.manifest.build_digest),
                                        build_digest=candidate.manifest.build_digest)
    assert report["status"] == "rejected"
    assert "parent_validation_deadline" in report["failures"]
    assert [attempt["status"] for attempt in report["attempts"]] == ["rejected", "not_run_parent_deadline"]
    assert Decimal(report["elapsed_seconds"]) < Decimal("1.5")


def test_protected_corpus_bytes_cannot_drift_after_the_validator_is_constructed(tmp_path):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    validator._corpus_bytes = validator._corpus_bytes.replace(b'"signal":"0"', b'"signal":"1"')
    with pytest.raises(PermissionError, match="held-out corpus"):
        validator.validate(candidate.manifest.build_digest)


def test_external_stage_tampering_during_genuine_replay_retains_rejected_attempts(tmp_path, monkeypatch):
    store, validator, manifest_pin, corpus_pin = prepared(tmp_path)
    candidate = stage(store, GOOD, manifest_pin, corpus_pin)
    source = store.root / "stages" / candidate.manifest.build_digest / "source.py"
    genuine_evaluate = validator._harness.evaluate

    def evaluate_then_external_tamper(*args, **kwargs):
        result = genuine_evaluate(*args, **kwargs)
        source.chmod(0o600)
        source.write_text(GOOD.replace("'0'", "'1'"))
        source.chmod(0o400)
        return result

    monkeypatch.setattr(validator._harness, "evaluate", evaluate_then_external_tamper)
    receipt = validator.validate(candidate.manifest.build_digest)
    assert store.receipt(receipt)["report"]["status"] == "rejected"
    assert store.receipt(receipt)["report"]["failures"] == ["stage_changed_during_validation"]
    assert len(store.receipt(receipt)["report"]["attempts"]) == 2
    with pytest.raises(ValueError, match="source content"):
        validator.verified_receipt(receipt, build_digest=candidate.manifest.build_digest)
    source.chmod(0o600)
    source.write_text(GOOD)
    source.chmod(0o400)
    assert validator.verified_receipt(receipt, build_digest=candidate.manifest.build_digest)["status"] == "rejected"
