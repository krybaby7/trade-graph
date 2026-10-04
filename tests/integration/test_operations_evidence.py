"""Retained operations facts fail closed; synthetic drills confer no deployment authority."""

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest
from tests.integration.test_dashboard_financial import _fill, _receipt, _runtime

from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.operations import private_backup
from trade_graph.application.operations_evidence import HostObservationCollector
from trade_graph.kernel.runtime_manifest import canonical_json

KEY = b"synthetic-local-observation-key-32"


def collector(tmp_path, monkeypatch, *, financial=False):
    runtime = _runtime(tmp_path)
    seed_paper_authority(runtime.database, runtime.clock, runtime.portfolio_id)
    if financial:
        _fill(runtime, "buy")
        receipt = _receipt(runtime)
        runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    runtime.database.path.chmod(0o600)
    tmp_path.chmod(0o700)
    for suffix in ("-wal", "-shm"):
        runtime.database.path.with_name(runtime.database.path.name + suffix).chmod(0o600)
    instance = HostObservationCollector(runtime.database.path, signing_key=KEY, clock=runtime.clock)
    monkeypatch.setattr(instance, "_service", lambda: {"status": "pending", "facts": {"systemd": False}})
    return runtime, instance


def test_missing_setup_is_retained_private_pending_without_network_or_database_creation(tmp_path, monkeypatch):
    instance = HostObservationCollector(tmp_path / "absent" / "paper.sqlite", signing_key=KEY)
    monkeypatch.setattr(instance, "_public", lambda: pytest.fail("public probe was not requested"))
    capture = instance.capture(tmp_path / "private" / "host.json")
    document = instance.verify(capture.path, capture.sha256).document
    assert not (tmp_path / "absent").exists()
    assert capture.path.stat().st_mode & 0o777 == 0o600
    assert capture.path.parent.stat().st_mode & 0o777 == 0o700
    assert document["checks"]["storage"]["status"] == "pending"
    assert "owner_budget" in document["checks"]["funded_setup"]["facts"]["missing"]
    assert document["owner_intended_host_verified"] is False
    assert document["immutable_image_verified"] is False
    assert document["immutable_mounts_verified"] is False
    assert document["checks"]["alerts"]["status"] == "pending"
    assert document["checks"]["off_host_backup"]["status"] == "pending"
    assert document["paid_calls"] is document["live_enabled"] is False
    with pytest.raises(FileExistsError):
        instance.capture(capture.path)


def test_actual_backup_restore_retains_complete_money_and_exact_owner_policy_bytes(tmp_path, monkeypatch):
    runtime, instance = collector(tmp_path, monkeypatch, financial=True)
    retained = instance.capture(tmp_path / "host.json", backup_restore=True)
    verified = instance.verify(retained.path, retained.sha256)
    document = verified.document
    policy = runtime.database.execute("SELECT * FROM owner_policy_revisions").fetchone()
    assert document["scope"]["policy_sha256"] == policy["content_hash"]
    assert document["scope"]["policy_sha256"] == hashlib.sha256(policy["document_json"].encode()).hexdigest()
    assert document["scope"]["database_snapshot_sha256"]
    assert document["checks"]["storage"]["status"] == "observed"
    assert document["checks"]["backup_restore"]["status"] == "observed"
    assert document["checks"]["backup_restore"]["facts"]["restored_projection_matches_backup"] is True
    assert document["checks"]["funded_setup"]["status"] == "pending"
    assert "persisted_owner_paid_permission" in document["checks"]["funded_setup"]["facts"]["missing"]
    assert runtime.database.execute("SELECT COUNT(*) FROM ledger_events WHERE kind='fill'").fetchone()[0] == 1
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    # Callers cannot mutate the authenticated view through a returned dictionary.
    document["owner_intended_host_verified"] = True
    assert verified.document["owner_intended_host_verified"] is False
    runtime.database.close()


@pytest.mark.parametrize("changed", ["money", "permissions", "inode", "policy"])
def test_current_database_or_filesystem_drift_invalidates_retained_observation(tmp_path, monkeypatch, changed):
    runtime, instance = collector(tmp_path, monkeypatch)
    retained = instance.capture(tmp_path / "host.json")
    if changed == "money":
        runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("2"), "later-change")
    elif changed == "permissions":
        runtime.database.path.chmod(0o644)
    elif changed == "inode":
        path = runtime.database.path
        runtime.database.close()
        replacement = tmp_path / "replacement.sqlite"
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
    else:
        runtime.database.execute("UPDATE owner_policy_revisions SET content_hash=?", ("0" * 64,))
    with pytest.raises(ValueError, match="sources changed"):
        instance.verify(retained.path, retained.sha256)
    if changed != "inode":
        runtime.database.close()


def test_private_sidecar_permissions_are_actual_storage_facts(tmp_path, monkeypatch):
    runtime, instance = collector(tmp_path, monkeypatch)
    runtime.database.path.with_name(runtime.database.path.name + "-wal").chmod(0o644)
    retained = instance.capture(tmp_path / "host.json")
    facts = instance.verify(retained.path, retained.sha256).document["checks"]["storage"]
    assert facts["status"] == "refused" and facts["facts"]["private_sidecars"] is False
    runtime.database.close()


@pytest.mark.parametrize("changed", ["digest", "key", "bytes", "restore", "backup", "checksum"])
def test_tampered_report_or_drill_sources_cannot_verify(tmp_path, monkeypatch, changed):
    runtime, instance = collector(tmp_path, monkeypatch)
    retained = instance.capture(tmp_path / "host.json", backup_restore=True)
    expected = retained.sha256
    if changed == "digest":
        expected = "0" * 64
    elif changed == "key":
        instance._key = b"different-synthetic-local-key-32x"
    elif changed == "bytes":
        retained.path.write_bytes(retained.path.read_bytes() + b" ")
    else:
        names = {"restore": "restored.sqlite", "backup": "snapshot.sqlite", "checksum": "snapshot.sqlite.sha256"}
        name = names[changed]
        path = tmp_path / "host.json.artifacts" / name
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="digest mismatch|authentication mismatch|artifact changed"):
        instance.verify(retained.path, expected)
    runtime.database.close()


def test_change_during_capture_is_retained_refused_not_silently_certified(tmp_path, monkeypatch):
    runtime, instance = collector(tmp_path, monkeypatch)

    def changed():
        runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("2"), "during-collection")
        return {"status": "pending", "facts": {}}

    monkeypatch.setattr(instance, "_service", changed)
    retained = instance.capture(tmp_path / "host.json")
    document = json.loads(retained.path.read_text())
    assert document["collection_consistent"] is False
    assert document["checks"]["storage"]["status"] == "refused"
    with pytest.raises(ValueError, match="inconsistent collection"):
        instance.verify(retained.path, retained.sha256)
    runtime.database.close()


def test_missing_policy_and_real_budget_remain_actionable_pending(tmp_path, monkeypatch):
    runtime, instance = collector(tmp_path, monkeypatch)
    runtime.database.execute("DELETE FROM mandates")
    runtime.database.execute("DELETE FROM owner_policy_revisions")
    runtime.database.execute("DELETE FROM deployment_budget")
    retained = instance.capture(tmp_path / "host.json")
    document = instance.verify(retained.path, retained.sha256).document
    missing = document["checks"]["funded_setup"]["facts"]["missing"]
    assert document["checks"]["storage"]["status"] == "observed"
    assert "persisted_owner_policy" in missing
    assert "available_separate_real_operating_allowance" in missing
    assert "selected_runtime_provider_credentials" in missing
    runtime.database.close()


def test_report_and_backup_bounds_refuse_before_publishing_false_evidence(tmp_path, monkeypatch):
    runtime, instance = collector(tmp_path, monkeypatch)
    monkeypatch.setattr("trade_graph.application.operations_evidence.MAX_DATABASE_BYTES", 1)
    retained = instance.capture(tmp_path / "host.json", backup_restore=True)
    document = instance.verify(retained.path, retained.sha256).document
    assert document["checks"]["storage"]["status"] == "unavailable"
    assert document["checks"]["backup_restore"]["status"] == "pending"
    with pytest.raises(ValueError, match="byte limit"):
        private_backup(runtime.database.path, tmp_path / "oversize.sqlite", maximum_bytes=1, wall_seconds=1)
    assert not (tmp_path / "oversize.sqlite").exists()
    runtime.database.close()


@pytest.mark.parametrize("seconds", [True, 0, float("inf"), float("nan")])
def test_backup_invalid_deadline_fails_before_creating_a_destination(tmp_path, seconds):
    with pytest.raises(ValueError, match="deadline"):
        private_backup(tmp_path / "absent.sqlite", tmp_path / "private" / "copy.sqlite", wall_seconds=seconds)
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("body", [{}, {"base": "EUR", "quote": "USD", "rate": "1", "date": "2026-01-01"},
                                  {"base": "USD", "quote": "EUR", "rate": "1", "date": "2099-01-01"}])
def test_public_success_requires_genuine_response_scope_and_date(tmp_path, monkeypatch, body):
    instance = HostObservationCollector(None, signing_key=KEY)
    monkeypatch.setattr("trade_graph.adapters.market.public.HttpxTextTransport.get_text",
                        lambda *args: json.dumps(body))
    result = instance._public()
    assert result["status"] == "unavailable"
    assert all(outcome["status"] == "refused" for outcome in result["facts"]["outcomes"])
    assert result["facts"]["portfolio_observations_created"] is False


@pytest.mark.parametrize("exception, category, status", [
    (httpx.ProxyError("private-proxy-and-key-value"), "proxy_connection_failed", None),
    (httpx.ReadTimeout("private-proxy-and-key-value"), "network_timeout", None),
    (httpx.ConnectError("private-proxy-and-key-value"), "network_transport_failed", None),
    (httpx.HTTPStatusError("private-proxy-and-key-value", request=httpx.Request("GET", "https://secret.invalid"),
                          response=httpx.Response(403)), "http_status_refused", 403),
    (ValueError("private-proxy-and-key-value"), "response_validation_refused", None),
])
def test_public_failures_retain_safe_category_timestamps_and_status_without_exception_text(
    monkeypatch, exception, category, status,
):
    instance = HostObservationCollector(None, signing_key=KEY)

    def unavailable(*args):
        raise exception

    monkeypatch.setattr("trade_graph.adapters.market.public.HttpxTextTransport.get_text", unavailable)
    result = instance._public()
    assert result["status"] == "unavailable"
    for outcome in result["facts"]["outcomes"]:
        assert outcome["failure_category"] == category
        assert outcome.get("http_status") == status
        assert outcome["completed_at"] >= outcome["attempted_at"]
        assert len(outcome["request_url_sha256"]) == 64
    assert "private-proxy-and-key-value" not in json.dumps(result)
    assert "secret.invalid" not in json.dumps(result)


def test_backup_deadline_aborts_copy_and_keeps_existing_records(tmp_path, monkeypatch):
    runtime, _ = collector(tmp_path, monkeypatch, financial=True)
    ticks = iter((0, 2))
    monkeypatch.setattr("trade_graph.application.operations.monotonic", lambda: next(ticks))
    destination = tmp_path / "snapshot.sqlite"
    with pytest.raises(ValueError, match="deadline exceeded"):
        private_backup(runtime.database.path, destination, maximum_bytes=16_777_216, wall_seconds=1)
    assert not destination.exists()
    assert not destination.with_suffix(".sqlite.sha256").exists()
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    runtime.database.close()


@pytest.mark.parametrize("claim", ["storage", "alerts", "off_host_backup", "owner_intended_host_verified"])
def test_even_local_key_holder_cannot_promote_unmeasured_external_or_storage_claim(tmp_path, claim):
    instance = HostObservationCollector(None, signing_key=KEY)
    retained = instance.capture(tmp_path / "private" / "host.json")
    document = json.loads(retained.path.read_text())
    document.pop("authentication")
    if claim == "owner_intended_host_verified":
        document[claim] = True
    else:
        document["checks"][claim]["status"] = "observed"
    document["authentication"] = {"scheme": "hmac-sha256", "signature": hmac.new(
        KEY, canonical_json(document).encode(), hashlib.sha256).hexdigest()}
    payload = canonical_json(document).encode()
    retained.path.write_bytes(payload)
    with pytest.raises(ValueError, match="authority|measured source|cannot be promoted"):
        instance.verify(retained.path, hashlib.sha256(payload).hexdigest())
