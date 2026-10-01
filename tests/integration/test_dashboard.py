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
