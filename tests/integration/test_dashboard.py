"""Dashboard projections stay labeled and owner writes stay authenticated."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock


def _client(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "dash.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "EUR", Decimal("100"), "open")
    execution = Execution(database, ledger, clock, PaperBroker(database, clock))
    runtime = type("R", (), {})()
    runtime.clock = clock
    runtime.database = database
    runtime.ledger = ledger
    runtime.portfolio_id = portfolio
    runtime.actual_spend = Decimal("1.25")
    ledger.add_expense(portfolio, expense_id="durable-operating-fixture", native_amount=Decimal("1.25"),
                       native_currency="EUR", reporting_amount=Decimal("1.25"), reporting_currency="EUR",
                       embedded=False, source="synthetic hosting fixture")
    runtime.execution = execution
    token, csrf = issue_session(database, clock, "owner")
    leader, _ = issue_session(database, clock, "leader")
    return TestClient(create_app(runtime)), database, portfolio, token, csrf, leader


def test_views_label_paper_results_and_redact_secrets(tmp_path) -> None:
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    database.execute(
        """INSERT INTO tasks
        (task_id, root_task_id, portfolio_id, role, objective, status, priority, max_steps,
         max_attempts, attempts_used, input_json, created_at)
        VALUES ('t1', 't1', ?, 'research', 'key sk-live-secret', 'QUEUED', 0, 1, 1, 0, '{}',
                '2026-01-01T00:00:00.000000Z')""",
        (portfolio,),
    )
    database.execute(
        """INSERT INTO order_intents
        (intent_id, portfolio_id, client_order_id, state, symbol, payload_json, created_at, updated_at)
        VALUES ('i1', ?, 'c1', 'UNKNOWN', 'BTC/USD',
                '{"client_order_id":"c1","symbol":"BTC/USD"}',
                '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')""",
        (portfolio,),
    )
    tasks = client.get("/api/v1/tasks", headers=headers)
    assert tasks.status_code == 200
    assert tasks.json()["tasks"][0]["objective"] == "[redacted]"
    assert "sk-live" not in tasks.text
    page = client.get("/organization", headers=headers)
    assert page.status_code == 200
    assert "[redacted]" in page.text
    assert "sk-live" not in page.text
    orders = client.get("/api/v1/orders", headers=headers)
    assert orders.json()["orders"][0]["degraded"] is True
    assert orders.json()["simulated"] is True
    costs = client.get("/costs", headers=headers)
    assert "Actual operating spend" in costs.text
    assert "1.25" in costs.text
    assert "does not refill" in costs.text
    changes = client.get("/changes", headers=headers)
    assert changes.status_code == 200
    denied = client.post(
        "/api/v1/leader/activate",
        headers={"Authorization": f"Bearer {leader}"},
        json={
            "candidate_id": "c",
            "baseline_hash": "a",
            "content_hash": "b",
            "attestation": {"runner": "trusted-controller"},
        },
    )
    assert denied.status_code == 422
    client.cookies.set("tg_session", token)
    paused = client.post("/api/v1/owner/pause", headers={"X-CSRF-Token": csrf},
                         json={"profile": "MANAGE_ONLY"})
    assert paused.status_code == 200
    resumed = client.post("/api/v1/owner/resume", headers={"X-CSRF-Token": csrf})
    assert resumed.status_code == 409
    assert "unresolved orders" in resumed.json()["detail"]["barriers"]
    assert client.get("/api/v1/health", headers=headers).json()["pause"]["profile"] == "MANAGE_ONLY"


def test_live_scope_labels_account_records_without_enabling_execution(tmp_path) -> None:
    from trade_graph.application.service_controller import ServiceController

    client, database, portfolio, token, _, _ = _client(tmp_path)
    runtime = client.app.state.runtime
    database.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (portfolio,))
    runtime.execution.mode = "live"
    runtime.service_controller = ServiceController(runtime, prerequisites=lambda: {
        "paper_available": False, "live_available": False, "ai_available": False,
        "reasons": ["Subscription process not provisioned."],
        "live_reasons": ["Protected owner mount is unverified."],
    })
    headers = {"Authorization": f"Bearer {token}"}
    for path in ["/", "/trading", "/costs", "/organization", "/changes", "/progress", "/owner"]:
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert "Live account operations" in response.text
        assert ">Live scope</span>" in response.text
        assert "Paper operations" not in response.text
        assert "Paper results are simulated." not in response.text
    overview = client.get("/", headers=headers).text
    assert "Allocated live capital" in overview
    assert "Current live portfolio value" in overview
    assert "Simulated trading P" not in overview
    assert "Simulated performance" not in overview
    trading = client.get("/trading", headers=headers).text
    assert "Simulated" not in trading
    owner = client.get("/owner", headers=headers).text
    assert "Protected live startup blocked" in owner
    assert "Protected owner mount is unverified." in owner
    progress = client.get("/progress", headers=headers).text
    assert "Protected owner mount is unverified." in progress
    assert "Subscription process not provisioned." in progress
    assert 'data-runtime-mode>Live' in progress
    assert client.get("/api/v1/costs", headers=headers).json()["simulated"] is False
    assert database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM graph_service_runs").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
