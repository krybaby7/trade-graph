"""Live home access and read-only review regression checks on isolated storage."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from tests.integration.test_dashboard import _client

from trade_graph.domain.clock import FrozenClock


@pytest.mark.parametrize("path",
                         ["/", "/progress", "/trading", "/organization", "/costs", "/owner", "/decisions/missing"])
def test_browser_pages_redirect_to_local_sign_in(tmp_path, path):
    client, *_ = _client(tmp_path)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "no-store" in response.headers["cache-control"]


def test_api_authentication_remains_json_401(tmp_path):
    client, *_ = _client(tmp_path)
    for path in ["/api/v1/overview", "/api/v1/tasks", "/api/v1/review-export"]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 401
        assert response.json()["detail"] == "authentication required"


def test_live_home_has_distinct_health_and_economic_indicators(tmp_path):
    client, _, _, token, *_ = _client(tmp_path)
    response = client.get("/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "Operational health" in response.text
    assert "Trading covers costs" in response.text
    assert "review-export" in response.text
    assert "data-live-home" in response.text
    data = client.get("/api/v1/overview", headers={"Authorization": f"Bearer {token}"}).json()
    assert data["economic_result"]["known_other_expense"] == "1.25"


def test_review_is_complete_scoped_readonly_and_consistent(tmp_path):
    client, database, portfolio, token, *_ = _client(tmp_path)
    runtime = client.app.state.runtime
    sql = """INSERT INTO tasks (task_id,root_task_id,portfolio_id,role,objective,status,priority,max_steps,
        max_attempts,attempts_used,input_json,created_at) VALUES (?,?,?,'trader','retained','FAILED',0,1,1,1,?,?)"""
    for index in range(230):
        database.execute(sql, (str(index), str(index), portfolio, json.dumps({"conversation": "PRIVATE TRANSCRIPT"}),
                               "2026-01-01T00:00:00.000000Z"))
    database.execute(sql, ("other", "other", "other-portfolio", "{}", "2026-01-01T00:00:00.000000Z"))
    def recovery():
        with sqlite3.connect(database.path) as writer:
            writer.execute(sql, ("later", "later", portfolio, "{}", "2026-01-01T00:00:00.000000Z"))
        return {"status": "PARTIAL_HISTORY", "unknown_additional_loss": True}
    runtime.recovery_history = recovery
    runtime.service_controller = type("Poison", (), {"prerequisites": lambda: pytest.fail("CLI readiness invoked")})()
    response = client.get("/api/v1/review-export", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    result = response.json()
    assert result["records"]["tasks"]["count"] == 230
    assert "later" not in [row["task_id"] for row in result["records"]["tasks"]["records"]]
    assert "PRIVATE TRANSCRIPT" not in response.text
    assert "sessions" not in result["records"]
    assert result["overview"]["economic_result"]["status"] == "unknown"
    assert result["external_observations"]["resources"]["observed_at"]
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 232


def test_completed_reporting_cutoff_keeps_current_operations_separate(tmp_path):
    client, _, _, token, *_ = _client(tmp_path)
    client.app.state.runtime.clock = FrozenClock(datetime(2026, 1, 3, tzinfo=UTC))
    response = client.get("/api/v1/overview", params={"end_at": "2026-01-02T00:00:00Z"},
                          headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    result = response.json()
    assert result["reporting_period"]["end_at"] == "2026-01-02T00:00:00.000000Z"
    assert result["as_of"] == "2026-01-03T00:00:00.000000Z"
    assert result["activity"]["as_of"] == result["as_of"]
    assert result["valuation"]["as_of"] == result["reporting_period"]["end_at"]
    for invalid in ["bad", "2026-01-04T00:00:00Z", "2025-12-01T00:00:00Z"]:
        assert client.get("/api/v1/overview", params={"end_at": invalid},
                          headers={"Authorization": f"Bearer {token}"}).status_code == 422
