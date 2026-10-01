"""Browser session login, loopback assembly and private session issuance."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_api_security_live import _Runtime

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.cli import main
from trade_graph.dashboard import dashboard_runtime, owner_session_file


def test_browser_login_requires_session_and_rejects_cross_origin(tmp_path):
    runtime = _Runtime(tmp_path)
    token, _ = issue_session(runtime.database, runtime.clock, "owner")
    client = TestClient(create_app(runtime))
    assert client.post("/api/v1/session", json={"session_token": "invalid"}).status_code == 401
    assert client.post("/api/v1/session", json={"session_token": token},
                       headers={"Origin": "https://untrusted.test"}).status_code == 403
    result = client.post("/api/v1/session", json={"session_token": token},
                         headers={"Origin": "http://testserver"})
    assert result.status_code == 200
    cookie = result.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert token not in result.text
    assert client.get("/api/v1/overview").status_code == 200
    assert client.get("/api/v1/overview").headers["cache-control"] == "no-store"
    assert client.post("/api/v1/owner/pause", json={"profile": "MANAGE_ONLY"}).status_code == 403


def test_private_file_is_reused_and_untrusted_files_cannot_be_overwritten(tmp_path):
    runtime = _Runtime(tmp_path)
    path = tmp_path / "private" / "session.json"
    owner_session_file(runtime, path)
    before = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    owner_session_file(runtime, path)
    assert path.read_bytes() == before
    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="private"):
        owner_session_file(runtime, path)
    assert path.read_bytes() == before
    other = tmp_path / "link.json"
    other.symlink_to(path)
    with pytest.raises(ValueError, match="private"):
        owner_session_file(runtime, other)


def test_dashboard_cli_starts_only_local_web_service_without_printing_token(tmp_path, monkeypatch, capsys):
    runtime = _Runtime(tmp_path)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))
    file = tmp_path / "session.json"
    assert main(["dashboard", "--database", str(runtime.database.path), "--session-file", str(file)]) == 0
    output = capsys.readouterr().out
    token = json.loads(file.read_text())["session_token"]
    assert token not in output and "/login" in output
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8000, "workers": 1, "access_log": False}
    assert runtime.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    assert runtime.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    with pytest.raises(SystemExit):
        main(["dashboard", "--host", "0.0.0.0"])


def test_dashboard_does_not_create_uninitialized_or_live_account(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="does not exist"):
        dashboard_runtime(missing)
    assert not missing.exists()
    runtime = _Runtime(tmp_path)
    runtime.database.execute("UPDATE portfolios SET mode = 'live'")
    with pytest.raises(ValueError, match="paper portfolio"):
        dashboard_runtime(Path(runtime.database.path), runtime.portfolio_id)


def test_dashboard_refuses_to_serve_journals_from_shared_directory(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    runtime = _Runtime(shared)
    with pytest.raises(ValueError, match="private directory"):
        dashboard_runtime(runtime.database.path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = private / "alias.sqlite"
    link.symlink_to(runtime.database.path)
    with pytest.raises(ValueError, match="private directory"):
        dashboard_runtime(link)
    assert shared.stat().st_mode & 0o777 == 0o755


def test_untrusted_query_validation_never_reflects_supplied_credentials(tmp_path):
    runtime = _Runtime(tmp_path)
    client = TestClient(create_app(runtime))
    response = client.get("/api/v1/tasks", params={"limit": "sk-synthetic-fixture"})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid request parameters"
    assert "sk-synthetic" not in response.text
