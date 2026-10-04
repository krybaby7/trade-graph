"""Actual private retention via installed command APIs; no credentialed operations."""

import hashlib
import json
from pathlib import Path

import pytest

from trade_graph.application.operations_evidence import HostObservationCollector
from trade_graph.application.operations_preflight import capture_preflight, main, verify_preflight


def _without_probes(monkeypatch):
    monkeypatch.setattr(HostObservationCollector, "_service", lambda self: {"status": "pending", "facts": {}})
    monkeypatch.setattr(HostObservationCollector, "_public", lambda self: pytest.fail("unrequested public contact"))


def test_capture_and_verify_keep_missing_setup_pending_and_retain_key_privately(tmp_path, monkeypatch):
    _without_probes(monkeypatch)
    report, key = tmp_path / "private" / "report.json", tmp_path / "private" / "observation.key"
    absent = tmp_path / "absent.sqlite"
    result = capture_preflight(absent, report, key)
    assert result.exit_code == 3 and result.summary["status"] == "recorded"
    assert result.summary["checks"]["funded_setup"] == "pending"
    assert not absent.exists()
    assert len(key.read_bytes()) == 32
    assert key.stat().st_mode & 0o777 == report.stat().st_mode & 0o777 == 0o600
    assert report.parent.stat().st_mode & 0o777 == 0o700
    verified = verify_preflight(absent, report, key, result.summary["evidence_sha256"])
    assert verified == result
    assert result.summary["paid_calls"] is False
    assert result.summary["funded_acceptance_verified"] is False
    assert key.read_bytes().hex() not in json.dumps(result.summary)


def test_retained_key_can_verify_multiple_records_and_no_record_is_overwritten(tmp_path, monkeypatch):
    _without_probes(monkeypatch)
    folder = tmp_path / "private"
    key = folder / "observation.key"
    first = capture_preflight(None, folder / "one.json", key)
    original = key.read_bytes()
    second = capture_preflight(None, folder / "two.json", key)
    assert key.read_bytes() == original
    assert verify_preflight(None, folder / "one.json", key, first.summary["evidence_sha256"]).exit_code == 3
    assert verify_preflight(None, folder / "two.json", key, second.summary["evidence_sha256"]).exit_code == 3
    with pytest.raises(ValueError, match="already exists"):
        capture_preflight(None, folder / "one.json", folder / "unused.key")
    assert not (folder / "unused.key").exists()


@pytest.mark.parametrize("problem", ["symlink", "public", "short", "oversize", "directory"])
def test_insecure_or_invalid_key_refuses_capture_without_record(tmp_path, monkeypatch, problem):
    _without_probes(monkeypatch)
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    key = folder / "key"
    if problem == "directory":
        key.mkdir(mode=0o700)
    elif problem == "symlink":
        target = folder / "target"
        target.write_bytes(b"a" * 32)
        target.chmod(0o600)
        key.symlink_to(target)
    else:
        key.write_bytes(b"a" * (1 if problem == "short" else 65 if problem == "oversize" else 32))
        key.chmod(0o644 if problem == "public" else 0o600)
    with pytest.raises((OSError, ValueError)):
        capture_preflight(None, folder / "report.json", key)
    assert not (folder / "report.json").exists()


def test_source_and_output_path_collision_refuses_before_key_or_evidence_creation(tmp_path):
    same = tmp_path / "new-private" / "same"
    with pytest.raises(ValueError, match="separate new paths"):
        capture_preflight(None, same, same)
    with pytest.raises(ValueError, match="separate new paths"):
        capture_preflight(same, tmp_path / "report.json", same)
    assert not same.parent.exists()


def test_command_outputs_only_redacted_summary_and_verification_does_not_create_missing_key(
    tmp_path, monkeypatch, capsys,
):
    _without_probes(monkeypatch)
    report, key = tmp_path / "private" / "report.json", tmp_path / "private" / "key"
    assert main(["capture", "--report", str(report), "--key", str(key)]) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "recorded"
    assert str(report) not in json.dumps(summary)
    assert main(["verify", "--report", str(report), "--key", str(key), "--sha256", "0" * 64]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "failure_type": "ValueError"}
    missing = tmp_path / "missing" / "key"
    assert main(["verify", "--report", str(report), "--key", str(missing),
                 "--sha256", hashlib.sha256(report.read_bytes()).hexdigest()]) == 2
    assert not missing.parent.exists()


def test_requested_unavailable_public_probe_is_recorded_with_nonzero_diagnostic_status(tmp_path, monkeypatch):
    _without_probes(monkeypatch)
    calls = []

    def probe(self):
        calls.append(True)
        return {"status": "unavailable", "facts": {"outcomes": []}}

    monkeypatch.setattr(HostObservationCollector, "_public", probe)
    result = capture_preflight(None, tmp_path / "private" / "report.json", tmp_path / "private" / "key",
                               public_data=True)
    assert result.exit_code == 2 and result.summary["status"] == "recorded"
    assert result.summary["checks"]["public_data"] == "unavailable"
    assert calls == [True]


def test_command_failure_never_logs_private_path_or_configuration_value(tmp_path, capsys):
    secret_path = Path(tmp_path) / "private-account-and-key-value"
    assert main(["verify", "--report", str(secret_path), "--key", str(secret_path), "--sha256", "invalid"]) == 2
    output = capsys.readouterr().out
    assert "private-account-and-key-value" not in output
    assert json.loads(output)["status"] == "refused"
