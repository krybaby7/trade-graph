"""Mission-control checks retain scoped, honest evidence without financial writes."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from trade_graph.api import progress_runs as runner


@pytest.fixture
def runtime(tmp_path):
    tmp_path.chmod(0o700)
    financial = tmp_path / "financial.sqlite"
    financial.write_bytes(b"financial-state-must-not-change")
    financial.chmod(0o600)
    return SimpleNamespace(database=SimpleNamespace(path=financial), portfolio_id="portfolio-a", deployment_id="dep-a")


def _wait(runtime, run_id):
    with runner._WORKERS_LOCK:
        thread = runner._WORKERS.get(run_id)
    if thread:
        thread.join(timeout=100)
        assert not thread.is_alive(), "bounded check worker failed to finish"
    return next(record for record in runner.runs(runtime) if record["run_id"] == run_id)


def _successful_script(path, run_id, update):
    root = path.parent / "progress-run-work"
    root.mkdir(mode=0o700, exist_ok=True)
    directory = root / run_id
    directory.mkdir(mode=0o700)
    (directory / "synthetic-test-fixture").write_text("unit-test fixture, not exchange evidence")
    update(0, "passed", "Synthetic unit-test fixture.")
    update(1, "passed", "Synthetic unit-test fixture.")
    return "Synthetic test fixture; no real exchange evidence."


def _responses():
    pair = {"base": "XXBT", "quote": "ZUSD", "pair_decimals": 1, "lot_decimals": 8, "ordermin": "0.0001"}
    return {
        "Time": {"unixtime": 1791158400, "rfc1123": "Mon, 05 Oct 2026 00:00:00 GMT"},
        "SystemStatus": {"status": "online", "timestamp": "2026-10-05T00:00:00Z"},
        "AssetPairs": {
            "XXBTZUSD": {**pair, "altname": "XBTUSD"},
            "XETHZUSD": {**pair, "base": "XETH", "altname": "ETHUSD"},
        },
        "Ticker": {
            "XXBTZUSD": {"a": ["100.1", "1", "1"], "b": ["100", "1", "1"]},
            "XETHZUSD": {"a": ["50.1", "1", "1"], "b": ["50", "1", "1"]},
        },
    }


def _mock_public(monkeypatch, handler):
    monkeypatch.setattr(
        runner,
        "_public_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=1,
            follow_redirects=False,
        ),
    )


def test_unknown_and_planned_checks_cannot_start(runtime):
    checks = runner.checks()
    assert {item["id"] for item in checks if item["available"]} == {"offline-loop", "kraken-public"}
    for check_id in ["../../config", "AddOrder", "kraken-account", "kraken-order-validation", "kraken-live-pilot"]:
        with pytest.raises(ValueError):
            runner.start(runtime, check_id)
    assert not (runtime.database.path.parent / "progress-runs.sqlite").exists()


def test_completed_runs_are_persistent_exact_scope_and_private(runtime, monkeypatch):
    monkeypatch.setattr(runner, "_offline", _successful_script)
    before = runtime.database.path.read_bytes()
    queued = runner.start(runtime, "offline-loop")
    assert queued["status"] == "queued"
    assert queued["finished_at"] is None
    completed = _wait(runtime, queued["run_id"])
    assert completed["status"] == "passed"
    assert completed["synthetic"] is True
    assert all(step["status"] == "passed" for step in completed["steps"])
    restarted = SimpleNamespace(**vars(runtime))
    assert runner.runs(restarted) == [completed]
    for scope in [{"portfolio_id": "portfolio-b"}, {"deployment_id": "dep-b"}]:
        other = SimpleNamespace(**(vars(runtime) | scope))
        assert runner.runs(other) == []
    assert runtime.database.path.read_bytes() == before
    assert (runtime.database.path.parent / "progress-runs.sqlite").stat().st_mode & 0o777 == 0o600


def test_one_active_per_scope_and_concurrent_other_scope(runtime, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked(path, run_id, update):
        update(0, "running", "Blocked synthetic test worker.")
        entered.set()
        assert release.wait(timeout=5)
        update(0, "passed", "Synthetic fixture.")
        update(1, "passed", "Synthetic fixture.")
        return "Synthetic fixture."

    monkeypatch.setattr(runner, "_offline", blocked)
    first = runner.start(runtime, "offline-loop")
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            runner.start(runtime, "kraken-public")
        other = SimpleNamespace(**(vars(runtime) | {"deployment_id": "dep-other"}))
        second = runner.start(other, "offline-loop")
        assert len(runner.runs(runtime)) == len(runner.runs(other)) == 1
    finally:
        release.set()
    assert _wait(runtime, first["run_id"])["status"] == "passed"
    assert _wait(other, second["run_id"])["status"] == "passed"


@pytest.mark.parametrize("expired", ["heartbeat", "deadline", "dead-process"])
def test_abandoned_runs_become_interrupted_never_green(runtime, monkeypatch, expired):
    monkeypatch.setattr(runner, "_offline", _successful_script)
    record = runner.start(runtime, "offline-loop")
    _wait(runtime, record["run_id"])
    path = runner._path(runtime)
    changes = {
        "heartbeat": (time.time() - runner._STALE_SECONDS - 1, time.time() + 500, os.getpid()),
        "deadline": (time.time(), time.time() - 1, os.getpid()),
        "dead-process": (time.time(), time.time() + 500, 999_999_999),
    }[expired]
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE progress_runs SET status='running', finished_at=NULL,
            steps_json=?, heartbeat=?, deadline=?, owner_pid=? WHERE run_id=?""",
            (
                json.dumps([{"label": "Abandoned check", "status": "running", "detail": "Pending"}]),
                *changes,
                record["run_id"],
            ),
        )
    recovered = runner.runs(SimpleNamespace(**vars(runtime)))[0]
    assert recovered["status"] == "interrupted"
    assert recovered["finished_at"] is not None
    assert recovered["steps"][0]["status"] == "interrupted"
    runner._update(path, record["run_id"], "passed", "Late worker result", [], finished=True)
    assert runner.runs(runtime)[0]["status"] == "interrupted"


def test_retention_removes_only_owned_generated_workspaces(runtime, monkeypatch):
    monkeypatch.setattr(runner, "_offline", _successful_script)
    root = runtime.database.path.parent / "progress-run-work"
    root.mkdir(mode=0o700)
    unrelated = root / "owner-notes"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    ids = []
    for _ in range(22):
        record = runner.start(runtime, "offline-loop")
        ids.append(record["run_id"])
        assert _wait(runtime, record["run_id"])["status"] == "passed"
    assert len(runner.runs(runtime)) == 20
    assert not (root / ids[0]).exists()
    assert not (root / ids[1]).exists()
    assert (root / ids[-1]).is_dir()
    assert (unrelated / "keep").read_text() == "keep"


def test_actual_packaged_offline_loop_never_touches_financial_database(runtime):
    before = runtime.database.path.read_bytes()
    record = runner.start(runtime, "offline-loop")
    result = _wait(runtime, record["run_id"])
    assert result["status"] == "passed", result
    assert result["synthetic"] is True
    assert "14 local scripted checks" in result["summary"]
    evidence = runtime.database.path.parent / "progress-run-work" / record["run_id"] / "evidence.json"
    assert evidence.stat().st_mode & 0o777 == 0o600
    report = json.loads(evidence.read_text())
    assert report["external_provider_calls"] == 0
    assert report["live_enabled"] is False
    assert runtime.database.path.read_bytes() == before


def test_offline_subprocess_scrubs_secrets_and_configuration(runtime, monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "private-unit-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "private-unit-test-key")
    monkeypatch.setenv("TRADE_GRAPH_CONFIG", "/private/owner-config.json")
    captured = {}

    class Process:
        def __init__(self, args, **kwargs):
            captured.update(args=args, **kwargs)
            report = {
                "checks": {f"scripted-{index}": True for index in range(14)},
                "passed": True,
                "paid_calls_enabled": False,
                "live_enabled": False,
                "external_provider_calls": 0,
            }
            Path(args[-1], "evidence.json").write_text(json.dumps(report))

        def wait(self, timeout):
            assert timeout == runner._OFFLINE_TIMEOUT
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    assert "14 local" in runner._offline(runner._path(runtime), "a" * 32, lambda *args: None)
    assert set(captured["env"]) == {"PATH", "LANG", "LC_ALL"}
    assert captured["args"][1:6] == ["-I", "-m", "trade_graph.cli", "demo", "--offline"]
    assert captured["cwd"] == Path(captured["args"][-1])
    assert captured["umask"] == 0o077
    assert "shell" not in captured


def test_public_uses_only_fixed_endpoints_no_credentials_and_persists(runtime, monkeypatch):
    observed = []
    responses = _responses()

    def transport(request):
        observed.append(request)
        return httpx.Response(200, json={"error": [], "result": responses[request.url.path.split("/")[-1]]})

    _mock_public(monkeypatch, transport)
    before = runtime.database.path.read_bytes()
    record = runner.start(runtime, "kraken-public")
    result = _wait(runtime, record["run_id"])
    assert result["status"] == "passed"
    assert result["synthetic"] is False  # Route is public; transport here is an injected test fixture.
    assert "No account access" in result["summary"]
    assert all("Received at" in step["detail"] for step in result["steps"])
    assert [request.url.path for request in observed] == [
        f"/0/public/{name}" for name in ["Time", "SystemStatus", "AssetPairs", "Ticker"]
    ]
    assert all(request.method == "GET" and request.url.host == "api.kraken.com" for request in observed)
    assert all("API-Key" not in request.headers and "Authorization" not in request.headers for request in observed)
    assert all("AddOrder" not in str(request.url) for request in observed)
    assert runtime.database.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["http", "redirect", "api-error", "shape", "json", "large", "network", "timeout"])
def test_public_failures_are_not_success_and_never_leak_raw_errors(runtime, monkeypatch, failure):
    observed = []

    def transport(request):
        observed.append(request)
        if failure == "network":
            raise httpx.ConnectError("private-unit-test-key", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-unit-test-key", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://evil.test/private-unit-test-key"})
        if failure == "http":
            return httpx.Response(403, text="private-unit-test-key")
        if failure == "api-error":
            return httpx.Response(200, json={"error": ["private-unit-test-key"], "result": {}})
        if failure == "shape":
            return httpx.Response(200, json={"error": [], "result": {"unixtime": "invalid"}})
        if failure == "json":
            return httpx.Response(200, content=b"private-unit-test-key")
        return httpx.Response(200, content=b"x" * (runner._MAX_RESPONSE_BYTES + 1))

    _mock_public(monkeypatch, transport)
    record = runner.start(runtime, "kraken-public")
    result = _wait(runtime, record["run_id"])
    assert result["status"] == "failed"
    assert result["steps"][0]["status"] == "failed"
    assert all(step["status"] == "interrupted" for step in result["steps"][1:])
    assert "private-unit-test-key" not in json.dumps(result)
    assert len(observed) == 1


def test_public_overall_deadline_cancels_even_a_delayed_transport(runtime, monkeypatch):
    async def delayed(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"error": [], "result": _responses()["Time"]})

    monkeypatch.setattr(runner, "_PUBLIC_TIMEOUT", 0.03)
    _mock_public(monkeypatch, delayed)
    started = time.monotonic()
    record = runner.start(runtime, "kraken-public")
    result = _wait(runtime, record["run_id"])
    assert time.monotonic() - started < 1
    assert result["status"] == "failed"
    assert "timed out" in result["summary"]


@pytest.mark.parametrize("case", ["symlink", "permissive", "hardlink", "parent"])
def test_storage_rejects_unsafe_paths(runtime, tmp_path, case):
    path = runtime.database.path.parent / "progress-runs.sqlite"
    if case == "symlink":
        path.symlink_to(runtime.database.path)
    elif case == "permissive":
        path.write_text("")
        path.chmod(0o644)
    elif case == "hardlink":
        os.link(runtime.database.path, path)
    else:
        tmp_path.chmod(0o755)
    with pytest.raises(RuntimeError):
        runner.runs(runtime)
    assert runtime.database.path.read_bytes() == b"financial-state-must-not-change"


def test_generic_worker_errors_are_redacted(runtime, monkeypatch):
    def broken(*args):
        raise ValueError("private-unit-test-key")

    monkeypatch.setattr(runner, "_offline", broken)
    result = _wait(runtime, runner.start(runtime, "offline-loop")["run_id"])
    assert result["status"] == "failed"
    assert "private-unit-test-key" not in json.dumps(result)


@pytest.mark.parametrize("field", ["pair_decimals", "lot_decimals"])
@pytest.mark.parametrize("value", [True, False, -1])
def test_public_rejects_boolean_or_negative_pair_precision(field, value):
    result = _responses()["AssetPairs"]
    result["XXBTZUSD"][field] = value
    with pytest.raises(runner._CheckFailure, match="precision"):
        runner._validate_public("AssetPairs", result, set())
