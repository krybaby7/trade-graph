"""An accepted observation gap stays visible without resetting account totals."""

from tests.integration.test_dashboard import _client


def test_dashboard_discloses_partial_history_and_new_period(tmp_path):
    client, database, portfolio, token, _, _ = _client(tmp_path)
    runtime = client.app.state.runtime
    incident = {
        "status": "PARTIAL_HISTORY",
        "incidents": [{"recovery_sha256": "a" * 64, "recorded_at": "2026-01-01T00:00:00Z",
            "incident": {"incident_id": "synthetic-gap", "affected_interval": {
            "start_at": "2025-12-31T12:00:00Z", "end_at": "2025-12-31T13:00:00Z"},
            "missing_records": [{"table": "valuation_marks", "record_id": "missing-mark", "observed_at": None}],
            "unknown_additional_loss": True}}],
        "active_evaluation_period": {"period_id": "recovered-period", "started_at": "2026-01-01T00:00:00Z"},
        "prior_evaluation_periods": [], "account_reset": False, "old_run_retained": True,
    }
    runtime.recovery_history = lambda: incident
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/overview", headers=headers)
    assert response.status_code == 200
    assert response.json()["recovery_history"] == incident
    assert response.json()["allocated_capital"]["native"][0]["amount"] == "100"
    page = client.get("/", headers=headers)
    assert page.status_code == 200
    assert "Historical observations are incomplete" in page.text
    assert "1 identified missing observations" in page.text
    assert "2025-12-31T12:00:00Z to 2025-12-31T13:00:00Z" in page.text
    assert "recovered-period" in page.text
    assert "2026-01-01T00:00:00Z" in page.text
    assert "Earlier account history and failures are retained" in page.text
    assert "Additional observation loss may be unknown" in page.text
    database.close()


def test_dashboard_does_not_claim_complete_history_when_verification_unavailable(tmp_path):
    client, database, _, token, _, _ = _client(tmp_path)
    client.app.state.runtime.recovery_history = lambda: {"status": "UNAVAILABLE"}
    headers = {"Authorization": f"Bearer {token}"}
    page = client.get("/", headers=headers)
    assert page.status_code == 200
    assert "Recovery history verification is unavailable" in page.text
    assert "Historical continuity is not certified" in page.text
    assert "Historical observations are incomplete" not in page.text
    database.close()
