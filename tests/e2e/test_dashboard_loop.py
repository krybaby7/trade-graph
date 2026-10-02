"""Owner dashboard navigates the actual credential-free improvement loop."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.dashboard import dashboard_runtime
from trade_graph.demo import run_offline
from trade_graph.domain.clock import FrozenClock


def test_dashboard_reconciles_full_loop_and_navigates_retained_evidence(tmp_path):
    work = tmp_path / "loop"
    work.mkdir(mode=0o700)
    report = run_offline(work)
    runtime = dashboard_runtime(work / "demo.sqlite", report["portfolio_id"])
    runtime.clock = FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))
    # A decoy runtime counter must never become a financial projection.
    runtime.actual_spend = Decimal("987.65")
    token, _ = issue_session(runtime.database, runtime.clock, "owner")
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(create_app(runtime))
    try:
        for route in ["overview", "positions", "orders", "tasks", "decisions", "research", "lessons",
                      "costs", "changes", "events"]:
            assert client.get(f"/api/v1/{route}").status_code == 401
            result = client.get(f"/api/v1/{route}", headers=headers)
            assert result.status_code == 200, (route, result.text)
            assert "/tmp/" not in result.text and "sk-" not in result.text
        overview = client.get("/api/v1/overview", headers=headers).json()
        assert Decimal(overview["equity"]) == runtime.ledger.equity(runtime.portfolio_id).equity
        assert overview["actual_spend"] != "987.65"
        assert overview["simulated"] is True
        assert overview["degraded"] is True  # Recorded historical marks are explicitly stale.
        costs = client.get("/api/v1/costs", headers=headers).json()
        assert overview["actual_spend"] == costs["actual_spend"]
        orders = client.get("/api/v1/orders", headers=headers).json()["orders"]
        assert sum(len(order["fills"]) for order in orders) == report["fill_count"] == 4
        decision = client.get(f"/api/v1/decisions/{report['leader_activation_decision_id']}", headers=headers)
        assert decision.status_code == 200
        candidate = client.get(f"/api/v1/changes/{report['engineer_candidate_id']}", headers=headers).json()
        assert candidate["candidate"]["state"] == "ROLLED_BACK"
        assert candidate["attestation"]["exit_code"] == 0
        assert candidate["artifacts"] and candidate["rollouts"]
        health = client.get("/api/v1/health", headers=headers).json()
        assert health["live_enabled"] is False and health["paid_calls_enabled"] is False
        for page in ["/", "/trading", "/organization", "/costs", "/changes", "/owner",
                     f"/changes/{report['engineer_candidate_id']}",
                     f"/decisions/{report['leader_activation_decision_id']}"]:
            rendered = client.get(page, headers=headers)
            assert rendered.status_code == 200, (page, rendered.text)
            assert "[private path]" in rendered.text or "/tmp/" not in rendered.text
        assert runtime.database.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 4
    finally:
        runtime.database.close()
