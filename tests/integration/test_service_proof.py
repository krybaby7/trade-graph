"""Real local processes, financial continuity and strict retained proof boundaries."""

import hashlib
import hmac
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from trade_graph.application.operations_evidence import HostObservationCollector
from trade_graph.application.service_proof import LocalPaperRestartCollector, LocalRestartSource, main
from trade_graph.kernel.runtime_manifest import canonical_json

KEY = b"synthetic-local-service-proof-key"


@pytest.fixture
def proof(tmp_path):
    collector = LocalPaperRestartCollector(signing_key=KEY)
    retained = collector.capture(tmp_path / "proof.json")
    return collector, retained


def resign(collector, retained, change):
    document = json.loads(retained.path.read_bytes())
    document.pop("authentication")
    change(document)
    document["authentication"] = {"scheme": "hmac-sha256", "signature": hmac.new(
        KEY, canonical_json(document).encode(), hashlib.sha256).hexdigest()}
    payload = canonical_json(document).encode()
    retained.path.write_bytes(payload)
    return collector.verify(retained.path, hashlib.sha256(payload).hexdigest())


def test_actual_crashed_service_recovers_once_and_two_cli_boots_preserve_pause_and_finance(proof, monkeypatch):
    collector, retained = proof
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("verification contacted a network"))
    document = collector.verify(retained.path, retained.sha256).document
    assert document["before"]["intent_state"] == "UNKNOWN"
    assert document["before"]["leases"] > 0 and document["before"]["fills"] == 0
    assert document["before"]["broker_fills"] == 1
    assert document["after"]["intent_state"] == "FILLED"
    assert document["after"]["attempts"] == document["after"]["fills"] == 1
    assert document["after"]["leases"] == document["after"]["real_receipts"] == 0
    assert document["after"]["owner_pause"] == ["MANAGE_ONLY", "owner"]
    assert all(run["exclusive_flock_observed"] and run["leases_drained"] and run["exit_code"] == 0
               for run in document["runs"])
    assert all(document[name] is False for name in ("paid_calls", "private_venue_calls", "live_enabled",
                                                   "actual_systemd_verified", "owner_intended_host_verified",
                                                   "funded_acceptance_verified"))
    assert retained.path.stat().st_mode & 0o777 == 0o600
    assert retained.path.parent.stat().st_mode & 0o777 == 0o700
    document["actual_systemd_verified"] = True
    assert collector.verify(retained.path, retained.sha256).document["actual_systemd_verified"] is False


@pytest.mark.parametrize("name", ["paper-config.json", "crashed.sqlite", "first-recovered.sqlite", "restarted.sqlite",
                                 "restarted.sqlite.sha256", "service-1.stdout", "service-2.stderr"])
def test_retained_artifact_byte_drift_is_refused(proof, name):
    collector, retained = proof
    artifact = retained.path.parent / (retained.path.name + ".artifacts") / name
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        collector.verify(retained.path, retained.sha256)


@pytest.mark.parametrize("name", ["actual_systemd_verified", "owner_intended_host_verified",
                                 "funded_acceptance_verified", "paid_calls", "private_venue_calls", "live_enabled"])
def test_even_observation_key_holder_cannot_promote_external_or_financial_authority(proof, name):
    collector, retained = proof
    with pytest.raises(ValueError, match="scope, authority"):
        resign(collector, retained, lambda document: document.update({name: True}))


@pytest.mark.parametrize("change", [
    lambda document: document["before"].update(attempts=0),
    lambda document: document["after"].update(fills=0),
    lambda document: document["runs"][0].update(exclusive_flock_observed=False),
    lambda document: document["runs"][1].update(command_sha256="0" * 64),
    lambda document: document.update(config_sha256="0" * 64),
    lambda document: document.update(scope="owner_intended_host"),
    lambda document: document.update(unmeasured_success=True),
])
def test_signed_unmeasured_inventory_command_and_scope_claims_are_refused(proof, change):
    collector, retained = proof
    with pytest.raises(ValueError):
        resign(collector, retained, change)


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "public", "ancestor"])
def test_report_storage_attacks_are_refused_before_read(proof, attack, tmp_path):
    collector, retained = proof
    if attack == "public":
        retained.path.chmod(0o644)
    elif attack == "ancestor":
        alias = tmp_path / "alias"
        alias.symlink_to(retained.path.parent, target_is_directory=True)
        with pytest.raises((ValueError, OSError)):
            collector.verify(alias / retained.path.name, retained.sha256)
        return
    else:
        raw = retained.path.read_bytes()
        retained.path.unlink()
        if attack == "fifo":
            os.mkfifo(retained.path)
        else:
            source = retained.path.parent / "source.json"
            source.write_bytes(raw)
            source.chmod(0o600)
            if attack == "symlink":
                retained.path.symlink_to(source)
            else:
                os.link(source, retained.path)
    with pytest.raises((ValueError, OSError)):
        collector.verify(retained.path, retained.sha256)


def test_local_proof_integrates_as_synthetic_host_fact_without_operating_restart_promotion(proof, tmp_path):
    collector, retained = proof
    source = LocalRestartSource(collector, retained.path, retained.sha256)
    host = HostObservationCollector(None, signing_key=KEY, restart_source=source)
    captured = host.capture(tmp_path / "host.json")
    document = host.verify(captured.path, captured.sha256).document
    local = document["checks"]["local_restart_rehearsal"]
    assert local["status"] == "observed"
    assert local["facts"]["scope"] == "new_synthetic_paper_fixture"
    assert local["facts"]["operating_database_restart_verified"] is False
    assert document["checks"]["backup_restore"]["status"] == "pending"
    assert document["checks"]["funded_setup"]["status"] == "pending"
    assert document["owner_intended_host_verified"] is False
    retained.path.write_bytes(retained.path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        host.verify(captured.path, captured.sha256)


def test_failed_crash_process_cannot_publish_success_or_reuse_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    collector = LocalPaperRestartCollector(signing_key=KEY)
    with pytest.raises(ValueError, match="crash preparation failed"):
        collector.capture(tmp_path / "failed.json")
    assert not (tmp_path / "failed.json").exists()
    with pytest.raises(FileExistsError):
        collector.capture(tmp_path / "failed.json")


def test_duplicate_json_fields_are_refused_even_with_expected_digest(proof):
    collector, retained = proof
    payload = retained.path.read_bytes().replace(b'"kind":', b'"kind":"forged","kind":', 1)
    retained.path.write_bytes(payload)
    with pytest.raises(ValueError, match="duplicate"):
        collector.verify(retained.path, hashlib.sha256(payload).hexdigest())


def test_key_digest_and_source_pins_are_mandatory(proof, monkeypatch):
    collector, retained = proof
    with pytest.raises(ValueError, match="digest mismatch"):
        collector.verify(retained.path, "0" * 64)
    wrong = LocalPaperRestartCollector(signing_key=b"different-synthetic-test-key-32x")
    with pytest.raises(ValueError, match="authentication mismatch"):
        wrong.verify(retained.path, retained.sha256)
    monkeypatch.setattr("trade_graph.application.service_proof.protected_package_sha256", lambda: "0" * 64)
    with pytest.raises(ValueError, match="producer changed"):
        collector.verify(retained.path, retained.sha256)


def test_installed_entry_prints_no_private_paths_or_exception_text(tmp_path, capsys):
    path = tmp_path / "private-report.json"
    code = main(["verify", "--report", str(path), "--key", str(tmp_path / "missing.key"), "--sha256", "0" * 64])
    assert code == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "refused", "failure_type": "FileNotFoundError"}
    assert str(path) not in canonical_json(output)


def test_capture_cannot_reuse_existing_report_or_shared_directory(tmp_path):
    collector = LocalPaperRestartCollector(signing_key=KEY)
    path = tmp_path / "existing.json"
    path.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="already exists"):
        collector.capture(path)
    assert path.read_bytes() == b"preserve"
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="owner-private"):
        collector.capture(directory / "report.json")
    assert not list(directory.iterdir())


def test_false_keys_and_fake_source_objects_are_refused_without_processes(tmp_path):
    with pytest.raises(ValueError):
        LocalPaperRestartCollector(signing_key=b"short")
    with pytest.raises(ValueError):
        LocalRestartSource(SimpleNamespace(verify=lambda *args: True), Path(tmp_path / "proof.json"), "0" * 64).verify()
