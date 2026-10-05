"""Mission Control reads persisted state and cannot grant trading authority."""

from fastapi.testclient import TestClient
from tests.integration.test_api_security_live import _Runtime

from trade_graph.api import progress_runs
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session


def _client(tmp_path):
    runtime = _Runtime(tmp_path)
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    return runtime, TestClient(create_app(runtime)), token, csrf


def test_progress_is_private_and_reading_it_starts_no_trades(tmp_path):
    runtime, client, token, _ = _client(tmp_path)
    assert client.get("/progress").status_code == 401
    assert client.get("/api/v1/progress").status_code == 401
    headers = {"Authorization": f"Bearer {token}"}
    page = client.get("/progress", headers=headers)
    assert page.status_code == 200
    assert "Mission Control" in page.text
    assert "/static/progress.css" in page.text
    assert "/static/progress.js" in page.text
    assert page.headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    projection = client.get("/api/v1/progress", headers=headers).json()
    assert projection["project"]["completed"] == 17
    assert projection["project"]["total"] == 23
    assert projection["service"]["status"] == "idle"
    assert projection["test_runs"] == []
    assert any(check["id"] == "kraken-live-pilot" and not check["available"] for check in projection["checks"])
    assert runtime.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert runtime.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    assert client.get("/api/v1/health", headers=headers).json()["live_enabled"] is False


def test_progress_refreshes_scoped_activity_and_redacts_secrets(tmp_path):
    runtime, client, token, _ = _client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    before = client.get("/api/v1/progress", headers=headers).json()
    assert next(item for item in before["departments"] if item["id"] == "research")["task_count"] == 0
    runtime.database.execute(
        """INSERT INTO tasks
        (task_id, root_task_id, portfolio_id, role, objective, status, priority, max_steps,
         max_attempts, attempts_used, input_json, created_at)
        VALUES ('mission-research', 'mission-research', ?, 'research', ?, 'QUEUED', 0, 1, 1, 0, '{}',
                '2026-01-01T00:00:00.000000Z')""",
        (runtime.portfolio_id, "Read key sk-synthetic-fixture"),
    )
    response = client.get("/api/v1/progress", headers=headers)
    assert response.status_code == 200
    research = next(item for item in response.json()["departments"] if item["id"] == "research")
    assert research["task_count"] == 1
    assert research["status"] == "queued"
    assert "sk-synthetic-fixture" not in response.text
    assert "sk-synthetic-fixture" not in client.get("/progress", headers=headers).text


def test_only_owner_with_csrf_can_start_allowlisted_checks(tmp_path, monkeypatch):
    runtime, client, token, csrf = _client(tmp_path)
    called = []

    def start(_runtime, check_id):
        called.append(check_id)
        return {"run_id": "run-fixture", "check_id": check_id, "status": "queued", "synthetic": True}

    monkeypatch.setattr(progress_runs, "start", start)
    path = "/api/v1/progress/tests/offline-loop/run"
    assert client.post(path, json={}).status_code == 401
    reader, _ = issue_session(runtime.database, runtime.clock, "reader")
    assert client.post(path, headers={"Authorization": f"Bearer {reader}"}, json={}).status_code == 403
    client.cookies.set("tg_session", token)
    assert client.post(path, json={}).status_code == 403
    assert called == []
    response = client.post(path, headers={"X-CSRF-Token": csrf}, json={})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert called == ["offline-loop"]


def test_unavailable_checks_and_busy_errors_do_not_reflect_private_details(tmp_path, monkeypatch):
    _, client, token, _ = _client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/progress/tests/kraken-live-pilot/run", headers=headers).status_code == 400
    assert client.post("/api/v1/progress/tests/unrecognized/run", headers=headers).status_code == 400

    def busy(*_args):
        raise RuntimeError("private /workspace/account/key.json sk-synthetic-fixture")

    monkeypatch.setattr(progress_runs, "start", busy)
    response = client.post("/api/v1/progress/tests/offline-loop/run", headers=headers)
    assert response.status_code == 409
    assert "sk-synthetic-fixture" not in response.text
    assert "/workspace" not in response.text


def test_reported_test_pass_cannot_complete_project_or_enable_live(tmp_path, monkeypatch):
    runtime, client, token, _ = _client(tmp_path)
    monkeypatch.setattr(progress_runs, "runs", lambda _: [{
        "run_id": "synthetic-fixture", "check_id": "offline-loop", "label": "Local rehearsal",
        "status": "passed", "summary": "Scripted local checks", "kind": "local",
        "steps": [], "synthetic": True, "started_at": None, "finished_at": None,
    }])
    headers = {"Authorization": f"Bearer {token}"}
    projection = client.get("/api/v1/progress", headers=headers).json()
    assert projection["project"]["completed"] == 17
    assert next(item for item in projection["project"]["tasks"] if item["id"] == "T20")["status"] == "blocked"
    assert client.get("/api/v1/health", headers=headers).json()["live_enabled"] is False
    assert runtime.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert projection["test_runs"][0]["synthetic"] is True


def test_unavailable_test_history_keeps_the_project_view_readable(tmp_path, monkeypatch):
    _, client, token, _ = _client(tmp_path)

    def unavailable(_runtime):
        raise RuntimeError("private sidecar unavailable")

    monkeypatch.setattr(progress_runs, "runs", unavailable)
    response = client.get("/api/v1/progress", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    projection = response.json()
    assert projection["project"]["completed"] == 17
    assert projection["test_storage_error"]
    assert all(not check["available"] for check in projection["checks"])
