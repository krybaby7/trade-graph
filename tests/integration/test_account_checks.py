"""Retained synthetic observations become redacted historical UI evidence only."""

import asyncio
import importlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_api_security_live import _Runtime
from tests.integration.test_venue_conformance import Fixture, _replace_report
from tests.test_dashboard_pages import render

from trade_graph.api import progress_runs
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session


def bridge():
    assert importlib.util.find_spec("trade_graph.api.account_checks") is not None, "Verified import bridge is missing"
    return importlib.import_module("trade_graph.api.account_checks")


@pytest.fixture
def observed(tmp_path):
    tmp_path.chmod(0o700)
    fixture = Fixture(tmp_path)
    capture = asyncio.run(fixture.collect())
    database = tmp_path / "financial.sqlite"
    database.write_bytes(b"financial bytes must remain unchanged")
    runtime = SimpleNamespace(database=SimpleNamespace(path=database),
                              portfolio_id=fixture.scope.portfolio_id, deployment_id=fixture.scope.deployment_id)
    return fixture, capture, runtime


def test_import_verifies_real_retained_fixture_and_redacts_private_sources(observed):
    fixture, capture, runtime = observed
    before = runtime.database.path.read_bytes()
    now = datetime.now(UTC)
    run = bridge().import_observation(runtime, capture, fixture.scope, now=now)
    assert run["check_id"] == "kraken-account"
    assert run["label"] == "Kraken read-only account check"
    assert run["status"] == "incomplete"
    assert run["synthetic"] is True
    account = run["account_observation"]
    assert account["historical"] is True and account["freshness"] == "fresh"
    assert account["transport_basis"] == "injected_transport"
    assert account["authenticated_private_read_count"] == 0
    assert account["source_current"] is True
    assert account["observation_finished_at"] == capture.verify(now=now).observation.finished_at.isoformat()
    assert "order_lookups" not in account["verified_completed_stages"]
    lookup = next(step for step in run["steps"] if step["label"] == "Requested order lookups")
    assert lookup["status"] == "not_requested"
    assert len(account["pending_checks"]) >= 9
    assert "Financial account reconciliation remains unverified." in account["pending_checks"]
    assert runtime.database.path.read_bytes() == before
    stored = (runtime.database.path.parent / "progress-runs.sqlite").read_bytes()
    published = json.dumps(run).encode()
    for private in (fixture.scope.account_id, str(capture.path), fixture.api_key,
                    capture.observation_sha256, fixture.grant.credential_binding_sha256,
                    "native_summary_sha256", "held_balances", "available_balances"):
        assert private.encode() not in stored and private.encode() not in published


def test_partial_verified_prefix_preserves_missing_stages(tmp_path):
    tmp_path.chmod(0o700)
    fixture = Fixture(tmp_path, maximum_requests=2)
    capture = asyncio.run(fixture.collect())
    runtime = SimpleNamespace(database=SimpleNamespace(path=tmp_path / "financial.sqlite"),
                              portfolio_id=fixture.scope.portfolio_id, deployment_id=fixture.scope.deployment_id)
    run = bridge().import_observation(runtime, capture, fixture.scope)
    assert run["account_observation"]["verified_completed_stages"] == ["instruments"]
    assert run["steps"][0]["status"] == "observed"
    assert all(step["status"] == "pending" for step in run["steps"][1:])
    assert run["synthetic"] and run["status"] != "passed"
    assert not runtime.database.path.exists()


@pytest.mark.parametrize("fault", ["stale", "tamper", "scope", "runtime", "source", "caller_flag", "naive"])
def test_unverifiable_or_wrong_scope_import_never_creates_history(observed, fault):
    fixture, capture, runtime = observed
    expected = fixture.scope
    now = datetime.now(UTC)
    if fault == "stale":
        now += timedelta(seconds=61)
    elif fault == "tamper":
        capture.path.chmod(0o600)
        capture.path.write_bytes(capture.path.read_bytes() + b" ")
    elif fault == "scope":
        expected = expected.model_copy(update={"account_id": "different-synthetic-account"})
    elif fault == "runtime":
        runtime.deployment_id = "different-deployment"
    elif fault == "source":
        capture = _replace_report(capture, fixture.collector_key,
                                  lambda payload: payload.update(collector_sha256="0" * 64))
    elif fault == "caller_flag":
        capture = {"verified": True, "authorized": True, "synthetic": False}
    elif fault == "naive":
        now = now.replace(tzinfo=None)
    with pytest.raises(ValueError):
        bridge().import_observation(runtime, capture, expected, now=now)
    assert not (runtime.database.path.parent / "progress-runs.sqlite").exists()


def test_unknown_signed_pending_reason_is_replaced_with_generic_label(observed):
    fixture, capture, runtime = observed
    marker = "synthetic-private-account-native-order-TX123"
    capture = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload["pending"].append(marker))
    run = bridge().import_observation(runtime, capture, fixture.scope)
    assert marker not in json.dumps(run)
    assert marker.encode() not in (runtime.database.path.parent / "progress-runs.sqlite").read_bytes()
    assert "Additional observation checks remain pending." in run["account_observation"]["pending_checks"]


def test_history_expiry_uses_original_observation_time_and_import_is_idempotent(observed, monkeypatch):
    fixture, capture, runtime = observed
    now = datetime.now(UTC)
    run = bridge().import_observation(runtime, capture, fixture.scope, now=now)
    again = bridge().import_observation(runtime, capture, fixture.scope, now=now + timedelta(seconds=1))
    assert again["run_id"] == run["run_id"]
    assert again["account_observation"]["verified_at"] == run["account_observation"]["verified_at"]
    monkeypatch.setattr(progress_runs, "_current_time", lambda: now + timedelta(seconds=61))
    history = progress_runs.runs(runtime)
    assert len(history) == 1
    assert history[0]["account_observation"]["freshness"] == "stale"
    assert history[0]["status"] == "stale"
    assert history[0]["finished_at"] == run["finished_at"]
    assert all(step["status"] != "observed" for step in history[0]["steps"])
    other = SimpleNamespace(**(vars(runtime) | {"portfolio_id": "another-portfolio"}))
    assert progress_runs.runs(other) == []


def test_import_keeps_old_sidecar_runs_and_bounds_metadata(observed, monkeypatch):
    fixture, capture, runtime = observed
    path = progress_runs._path(runtime)
    with progress_runs._connection(path) as connection:
        connection.execute("INSERT INTO progress_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           ("a" * 32, runtime.portfolio_id, runtime.deployment_id, "offline-loop", "passed",
                            "2000-01-01T00:00:00Z", "2000-01-01T00:00:01Z", "Synthetic old run", "[]",
                            0, 0, "old", 0))
    monkeypatch.setattr(progress_runs, "_KEEP_SCOPE", 3)
    initial = bridge().import_observation(runtime, capture, fixture.scope)
    history = progress_runs.runs(runtime)
    assert next(run for run in history if run["run_id"] == "a" * 32)["synthetic"] is True
    for _ in range(4):
        capture = asyncio.run(fixture.collect())
        bridge().import_observation(runtime, capture, fixture.scope)
    assert len(progress_runs.runs(runtime)) == 3
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM progress_account_observations").fetchone()[0] == 3
        assert connection.execute("SELECT 1 FROM progress_runs WHERE run_id=?", (initial["run_id"],)).fetchone() is None


@pytest.mark.parametrize("role", ["owner", "reader"])
def test_account_mission_stays_disabled_and_displays_historical_evidence(observed, role):
    fixture, capture, runtime = observed
    run = bridge().import_observation(runtime, capture, fixture.scope)
    check = next(item for item in progress_runs.checks() if item["id"] == "kraken-account")
    page = render("progress.html", {"checks": [check], "test_runs": [run]}, role=role)
    action = page.split('data-run-check="kraken-account"', 1)
    assert len(action) == 2 and "disabled" in action[1].split(">", 1)[0]
    assert "uv run trade-graph kraken-read-only --database runtime/trade_graph.sqlite" in page
    assert "--owner-directory ~/.local/share/trade-graph-owner/kraken --symbol BTC/USD" in page
    assert "Historical observation" in page and "Synthetic injected transport" in page
    assert "Financial account reconciliation remains unverified." in page
    script = (Path(__file__).resolve().parents[2] / "src/trade_graph/web/static/progress.js").read_text()
    assert 'button.dataset.runCheck === "kraken-account"' in script
    assert "account_observation" in script and "textContent" in script


def test_api_does_not_accept_browser_account_import_or_change_financial_state(tmp_path):
    tmp_path.chmod(0o700)
    runtime = _Runtime(tmp_path)
    client = TestClient(create_app(runtime))
    owner, _ = issue_session(runtime.database, runtime.clock, "owner")
    headers = {"Authorization": f"Bearer {owner}"}
    before = runtime.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0]
    assert client.post("/api/v1/progress/tests/kraken-account/run", headers=headers).status_code == 400
    assert client.post("/api/v1/progress/account-observations", headers=headers,
                       json={"verified": True}).status_code == 404
    assert runtime.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == before
    runtime.database.close()


@pytest.mark.parametrize("fault", ["extra", "stage", "transport", "naive", "bad_json", "private_pending"])
def test_tampered_sidecar_metadata_fails_closed_or_uses_generic_labels(observed, fault):
    fixture, capture, runtime = observed
    run = bridge().import_observation(runtime, capture, fixture.scope)
    path = progress_runs._path(runtime)
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT metadata_json FROM progress_account_observations WHERE run_id=?",
                                 (run["run_id"],)).fetchone()[0]
        metadata = json.loads(raw)
        if fault == "extra":
            metadata["private_account_id"] = "synthetic-private-marker"
        elif fault == "stage":
            metadata["verified_completed_stages"] = ["synthetic-private-marker"]
        elif fault == "transport":
            metadata["transport_basis"] = "synthetic-private-marker"
        elif fault == "naive":
            metadata["observation_finished_at"] = "2026-10-05T00:00:00"
        elif fault == "private_pending":
            metadata["pending_codes"].append("synthetic-private-marker")
        stored = "{" if fault == "bad_json" else json.dumps(metadata)
        connection.execute("UPDATE progress_account_observations SET metadata_json=? WHERE run_id=?",
                           (stored, run["run_id"]))
    if fault == "private_pending":
        assert "synthetic-private-marker" not in json.dumps(progress_runs.runs(runtime))
    else:
        with pytest.raises(RuntimeError, match="private check registry is unavailable"):
            progress_runs.runs(runtime)


def test_naive_read_clock_cannot_promote_stored_capture(observed, monkeypatch):
    fixture, capture, runtime = observed
    bridge().import_observation(runtime, capture, fixture.scope)
    monkeypatch.setattr(progress_runs, "_current_time", lambda: datetime.now(UTC).replace(tzinfo=None))
    with pytest.raises(RuntimeError, match="private check registry is unavailable"):
        progress_runs.runs(runtime)


def test_distinct_sources_with_same_public_projection_do_not_collide(observed):
    fixture, capture, runtime = observed
    first = bridge().import_observation(runtime, capture, fixture.scope)
    changed = _replace_report(capture, fixture.collector_key,
                              lambda payload: payload.update(observation_id="different-synthetic-observation"))
    second = bridge().import_observation(runtime, changed, fixture.scope)
    assert first["run_id"] != second["run_id"]
    assert first["started_at"] == second["started_at"] and first["finished_at"] == second["finished_at"]
    assert len(progress_runs.runs(runtime)) == 2


def test_public_instrument_prefix_does_not_claim_an_authenticated_account_read(observed):
    fixture, capture, runtime = observed
    run = bridge().import_observation(runtime, capture, fixture.scope)
    metadata = run["account_observation"]
    metadata = {key: value for key, value in metadata.items()
                if key not in {"historical", "freshness", "pending_checks"}}
    metadata.update(transport_basis="owned_https", verified_completed_stages=["instruments"],
                    authenticated_private_read_count=0, pending_codes=[])
    # A projection-only wording check cannot establish actual HTTPS evidence.
    projected = bridge().project_history(run, metadata, now=datetime.now(UTC))
    assert "account read" not in projected["steps"][0]["detail"].lower()
    assert len(projected["account_observation"]["pending_checks"]) >= 9
    assert "Actual authenticated private connectivity remains unverified." in (
        projected["account_observation"]["pending_checks"])
    assert "Financial account reconciliation remains unverified." in projected["account_observation"]["pending_checks"]
