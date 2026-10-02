"""Operator reports use real durable records without spending or migrating."""

import fcntl
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.integration.test_dashboard_financial import _fill, _receipt, _reservation, _runtime

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ledger import Ledger
from trade_graph.application.operations import doctor_report, financial_report, offline_restore, private_backup
from trade_graph.contracts.models import ModelUsage
from trade_graph.domain.clock import FrozenClock, utc_iso


def _hashes(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.iterdir() if path.is_file() and not path.name.endswith("-shm")}


def test_missing_database_doctor_and_report_do_not_create_directories(tmp_path):
    missing = tmp_path / "absent" / "data.sqlite"
    report = doctor_report(missing)
    assert report["status"] == "error"
    assert report["database"]["present"] is False
    assert report["credentialed_providers"] == {"status": "pending", "performed": False}
    assert report["public_data_probe"] == {"status": "pending", "performed": False}
    with pytest.raises(ValueError, match="does not exist"):
        financial_report(missing)
    assert not missing.parent.exists()


@pytest.mark.parametrize("payload", [b"not a sqlite database", b"SQLite format 3\x00broken"])
def test_corrupt_database_is_not_reported_as_healthy_or_changed(tmp_path, payload):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(payload)
    before = _hashes(tmp_path)
    assert doctor_report(path)["status"] == "error"
    with pytest.raises(ValueError, match="could not be read safely"):
        financial_report(path)
    assert _hashes(tmp_path) == before


def test_old_schema_is_reported_without_migrating(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version TEXT, applied_at TEXT)")
        connection.execute("INSERT INTO schema_migrations VALUES ('0001', 'old')")
    before = _hashes(tmp_path)
    report = doctor_report(path)
    assert report["status"] == "error"
    assert report["database"]["schema"]["status"] == "unsupported"
    assert "0002" in report["database"]["schema"]["missing"]
    with pytest.raises(ValueError, match="schema is unsupported"):
        financial_report(path)
    assert _hashes(tmp_path) == before


def test_financial_report_preserves_native_economic_and_actual_synthetic_costs(tmp_path):
    runtime = _runtime(tmp_path)
    _fill(runtime, "buy")
    runtime.clock.advance(1)
    _fill(runtime, "sell", side="sell", price="44", fee="0.44")
    actual = _receipt(runtime)
    runtime.budget.allocate(actual, {runtime.portfolio_id: Decimal("1")})
    _receipt(runtime, synthetic=True, role="engineer")
    reservation = _reservation(runtime)
    runtime.budget.mark_uncertain(reservation)
    runtime.database.execute("INSERT INTO process_leases VALUES ('paper-service', 'local-worker', '2099')")
    report = financial_report(runtime.database.path, clock=runtime.clock)
    assert report["overview"]["performance"]["trading_pnl"] == "3.16"
    assert report["overview"]["performance"]["net_economic_pnl"] is None
    assert report["costs"]["actual_spend"] == "0.9"
    assert report["costs"]["synthetic_spend"] == "0.9"
    assert report["costs"]["allocated_actual_spend"] == "0.9"
    assert report["costs"]["summary"]["uncertain"] == "0.9"
    assert report["costs"]["uncertain_reservations"] == 1
    assert report["overview"]["performance"]["trading_fees"]["native"] == [{"asset": "EUR", "amount": "0.84"}]
    assert report["health"]["process_leases"][0]["expired"] is False
    assert report["health"]["uncertain_usage"] == 1
    assert report["verification"]["funded_paper_soak"] == "pending"
    assert report["paid_calls_enabled"] is False
    assert report["live_enabled"] is False
    # Resolving uncertainty yields the deterministic economic result, while
    # synthetic work never reduces economic P&L or the real budget.
    runtime.budget.commit(reservation, ModelUsage(uncached_input_tokens=0, billed_output_tokens=0),
                          provider="scripted", model="scripted", fx_rate=Decimal("0.9"))
    report = financial_report(runtime.database.path, clock=runtime.clock)
    assert report["overview"]["performance"]["net_economic_pnl"] == "2.26"
    runtime.database.close()


def test_idle_diagnostics_and_report_do_not_change_files_or_create_sqlite_sidecars(tmp_path):
    runtime = _runtime(tmp_path)
    path, clock = runtime.database.path, runtime.clock
    runtime.database.close()
    path.chmod(0o600)
    tmp_path.chmod(0o700)
    before = _hashes(tmp_path)
    report = doctor_report(path, clock=clock)
    assert report["status"] == "degraded"
    assert report["database"]["integrity"] == "ok"
    assert report["database"]["schema"]["status"] == "current"
    assert report["database"]["storage"]["private_directory"]
    assert report["database"]["storage"]["private_file"]
    assert report["configuration"]["status"] == "unconfigured"
    assert report["market"]["stored_observations"] == 0
    assert financial_report(path, clock=clock)["overview"]["equity"] == "100"
    assert _hashes(tmp_path) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(before)


def test_running_wal_report_reads_committed_state_without_financial_mutation(tmp_path):
    runtime = _runtime(tmp_path)
    path = runtime.database.path
    before = _hashes(tmp_path)
    report = financial_report(path, clock=runtime.clock)
    assert report["overview"]["equity"] == "100"
    assert _hashes(tmp_path) == before
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("25"), "after-first-report")
    assert financial_report(path, clock=runtime.clock)["overview"]["equity"] == "125"
    runtime.database.close()


def test_doctor_redacts_secrets_and_ignores_credentials_in_environment(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-environment-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-private-anthropic-key")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "models": {"paid_calls_enabled": False, "role_routes": {"trader": "dashboard-synthetic-card"},
                   "approved_price_card_ids": ["dashboard-synthetic-card"]},
    }))
    config_path.chmod(0o600)
    runtime.database.execute("UPDATE price_cards SET document_json = replace(document_json, 'scripted', ?)",
                             ("sk-private-database-key",))
    report = doctor_report(runtime.database.path, clock=runtime.clock, config_path=config_path)
    serialized = json.dumps(report)
    for secret in ("sk-private", "private-opaque-secret"):
        assert secret not in serialized
    assert report["credentialed_providers"]["status"] == "pending"
    assert report["configuration"]["credentials_inspected"] is False
    assert report["configuration"]["routing"]["status"] == "pending"
    assert report["configuration"]["routing"]["roles"][1]["ready"] is False
    config_path.write_text('{"token":"sk-do-not-leak"')
    report = doctor_report(runtime.database.path, clock=runtime.clock, config_path=config_path)
    assert report["configuration"]["status"] == "invalid"
    assert "sk-do-not-leak" not in json.dumps(report)
    runtime.database.close()


def test_stale_marks_are_provisional_and_never_filled_with_zero_pnl(tmp_path):
    runtime = _runtime(tmp_path)
    _fill(runtime, "buy")
    runtime.ledger.observe_mark(runtime.portfolio_id, "TEST", quote="EUR", price=Decimal("44"),
                                convention="bid", stale=False, source="synthetic fixture")
    runtime.clock.advance(31)
    report = financial_report(runtime.database.path, clock=runtime.clock)
    assert report["overview"]["provisional"] is True
    assert report["overview"]["performance"]["trading_pnl"] is None
    assert report["positions"]["positions"][0]["mark"]["stale"] is True
    assert report["health"]["degraded"] is True
    runtime.database.close()


def test_report_selects_paper_portfolio_explicitly_and_refuses_unknown_id(tmp_path):
    runtime = _runtime(tmp_path)
    second = runtime.ledger.create_portfolio(reporting_currency="EUR")
    runtime.ledger.deposit(second, "EUR", Decimal("75"), "second-opening")
    assert financial_report(runtime.database.path, clock=runtime.clock)["overview"]["equity"] == "75"
    assert financial_report(runtime.database.path, clock=runtime.clock,
                            portfolio_id=runtime.portfolio_id)["overview"]["equity"] == "100"
    with pytest.raises(ValueError, match="paper portfolio is required"):
        financial_report(runtime.database.path, portfolio_id="unknown")
    runtime.database.close()


def test_private_backup_and_restore_preserve_wal_state_and_permissions(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("25"), "committed-wal")
    destination = tmp_path / "private" / "backup.sqlite"
    digest = private_backup(runtime.database.path, destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    assert destination.with_suffix(".sqlite.sha256").read_text().strip() == digest
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert destination.with_suffix(".sqlite.sha256").stat().st_mode & 0o777 == 0o600
    restored = destination.parent / "restored.sqlite"
    assert offline_restore(destination, restored, offline_confirmed=True)["integrity"] == "ok"
    assert financial_report(restored, clock=runtime.clock)["overview"]["equity"] == "125"
    assert restored.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="must be new"):
        private_backup(runtime.database.path, destination)
    runtime.database.close()


def test_restore_requires_offline_and_refuses_service_flock_and_sidecars(tmp_path):
    runtime = _runtime(tmp_path)
    backup = tmp_path / "private" / "backup.sqlite"
    private_backup(runtime.database.path, backup)
    destination = backup.parent / "restored.sqlite"
    with pytest.raises(ValueError, match="explicit offline"):
        offline_restore(backup, destination)
    assert not destination.exists()
    offline_restore(backup, destination, offline_confirmed=True)
    before = destination.read_bytes()
    with destination.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="held by an operating service"):
            offline_restore(backup, destination, offline_confirmed=True)
    Path(str(destination) + "-wal").write_text("newer financial state")
    with pytest.raises(ValueError, match="without SQLite sidecars"):
        offline_restore(backup, destination, offline_confirmed=True)
    assert destination.read_bytes() == before
    assert Path(str(destination) + "-wal").read_text() == "newer financial state"
    runtime.database.close()


def test_corrupt_backup_restore_preserves_existing_destination(tmp_path):
    runtime = _runtime(tmp_path)
    backup = tmp_path / "private" / "backup.sqlite"
    private_backup(runtime.database.path, backup)
    destination = backup.parent / "preserved.sqlite"
    destination.write_bytes(b"prior data")
    backup.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        offline_restore(backup, destination, offline_confirmed=True)
    assert destination.read_bytes() == b"prior data"
    runtime.database.close()


def test_backup_refuses_missing_sources_and_public_storage(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="existing regular database"):
        private_backup(missing, tmp_path / "new" / "backup.sqlite")
    assert not missing.exists()
    assert not (tmp_path / "new").exists()
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "source.sqlite")
    Ledger(database, clock).create_portfolio(reporting_currency="EUR")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        private_backup(database.path, public / "backup.sqlite")
    with pytest.raises(ValueError, match="differ from the source"):
        private_backup(database.path, database.path)
    database.close()


def test_reports_make_no_network_connections(tmp_path, monkeypatch):
    import socket

    runtime = _runtime(tmp_path)

    def denied(*args, **kwargs):
        raise AssertionError("operator diagnostics attempted network access")

    monkeypatch.setattr(socket.socket, "connect", denied)
    assert doctor_report(runtime.database.path, clock=runtime.clock)["status"] != "error"
    assert financial_report(runtime.database.path, clock=runtime.clock)["read_only"] is True
    runtime.database.close()


def test_report_contains_every_receipt_and_reservation_beyond_first_page(tmp_path):
    runtime = _runtime(tmp_path)
    for _ in range(205):
        _receipt(runtime, tokens=1, synthetic=True)
    report = financial_report(runtime.database.path, clock=runtime.clock)
    assert len(report["costs"]["receipts"]) == 205
    assert len(report["costs"]["reservations"]) == 205
    assert len({receipt["receipt_id"] for receipt in report["costs"]["receipts"]}) == 205
    assert report["costs"]["pagination"] == {"total": 205, "complete": True}
    assert report["costs"]["actual_spend"] == "0"
    assert report["costs"]["synthetic_spend"] == "0.0001845"
    runtime.database.close()


def test_every_projection_uses_same_database_read_snapshot(tmp_path, monkeypatch):
    from trade_graph.api import financial

    runtime = _runtime(tmp_path)
    original = financial.overview
    updated = False

    def update_after_overview(reader):
        nonlocal updated
        result = original(reader)
        if not updated:
            runtime.ledger.deposit(runtime.portfolio_id, "EUR", Decimal("25"), "concurrent-write")
            updated = True
        return result

    monkeypatch.setattr(financial, "overview", update_after_overview)
    report = financial_report(runtime.database.path, clock=runtime.clock)
    assert report["overview"]["equity"] == "100"
    assert report["positions"]["balances"][0]["owned"] == "100"
    assert financial_report(runtime.database.path, clock=runtime.clock)["overview"]["equity"] == "125"
    runtime.database.close()


def test_doctor_distinguishes_local_routing_readiness_from_unperformed_probes(tmp_path):
    runtime = _runtime(tmp_path)
    config = tmp_path / "config.json"
    roles = ("research", "trader", "learning", "optimisation", "leader", "engineer")
    config.write_text(json.dumps({
        "models": {"paid_calls_enabled": False, "approved_price_card_ids": ["dashboard-synthetic-card"],
                   "role_routes": {role: "dashboard-synthetic-card" for role in roles}},
    }))
    config.chmod(0o600)
    at = utc_iso(runtime.clock.now())
    runtime.database.execute("INSERT INTO observations VALUES ('quote', 'paper', 'TEST/EUR', ?, ?, '{}', 1)",
                             (at, at))
    report = doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)
    assert report["status"] == "ok"
    assert report["configuration"]["routing"]["status"] == "ready"
    assert report["market"]["stale"] is False
    assert report["credentialed_providers"]["status"] == "pending"
    assert report["funded_paper_soak"]["status"] == "pending"
    runtime.clock.advance(31)
    report = doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)
    assert report["status"] == "degraded"
    assert report["market"]["stale"] is True
    assert "persisted market observations are stale" in report["warnings"]
    runtime.database.close()


@pytest.mark.parametrize("age_days,ready", [(0, True), (30, True), (31, False), (-1, False)])
def test_paid_route_price_age_is_local_evidence_and_does_not_probe_credentials(tmp_path, age_days, ready):
    from datetime import timedelta

    runtime = _runtime(tmp_path)
    row = runtime.database.execute("SELECT document_json FROM price_cards").fetchone()
    card = json.loads(row["document_json"])
    card.update(provider="openai", model="gpt-6.1-sol",
                verified_at=(runtime.clock.now().date() - timedelta(days=age_days)).isoformat())
    runtime.database.execute("UPDATE price_cards SET document_json = ?", (json.dumps(card),))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"models": {
        "paid_calls_enabled": True, "approved_price_card_ids": [card["price_card_id"]],
        "role_routes": {"trader": card["price_card_id"]},
    }}))
    config.chmod(0o600)
    report = doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)
    route = report["configuration"]["routing"]["roles"][1]
    assert route["ready"] is ready
    assert route["price_verification_age_days"] == age_days
    assert route["credential_probe"] == "pending"
    assert report["configuration"]["paid_calls_configured"] is True
    assert report["paid_calls_enabled"] is False
    assert report["credentialed_providers"]["performed"] is False
    runtime.database.close()


def test_doctor_uses_runtime_config_privacy_and_bounds_and_ignores_unknown_secret_fields(tmp_path):
    runtime = _runtime(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"models": None}))
    config.chmod(0o644)
    assert doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)["configuration"][
        "status"] == "invalid"
    config.chmod(0o600)
    assert doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)["configuration"][
        "status"] == "unconfigured"
    config.write_text(json.dumps({"models": {"fx_rate": "-1"}, "api_key": "sk-private-config-key"}))
    report = doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)
    assert report["configuration"]["status"] == "invalid"
    assert "sk-private-config-key" not in json.dumps(report)
    config.write_text(json.dumps({"models": {"fx_rate": "-1"}}))
    assert doctor_report(runtime.database.path, config_path=config, clock=runtime.clock)["configuration"][
        "status"] == "invalid"
    runtime.database.close()


def test_interrupted_wal_without_shared_memory_requires_recovery_and_is_not_mutated(tmp_path):
    runtime = _runtime(tmp_path)
    source = runtime.database.path
    copy = tmp_path / "interrupted.sqlite"
    copy.write_bytes(source.read_bytes())
    Path(str(copy) + "-wal").write_bytes(Path(str(source) + "-wal").read_bytes())
    before = _hashes(tmp_path)
    assert doctor_report(copy, clock=runtime.clock)["status"] == "error"
    with pytest.raises(ValueError, match="recovery is pending"):
        financial_report(copy, clock=runtime.clock)
    assert _hashes(tmp_path) == before
    assert not Path(str(copy) + "-shm").exists()
    runtime.database.close()
