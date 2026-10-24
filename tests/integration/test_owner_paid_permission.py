"""Explicit owner funded permission stays separate from paper capital and keys."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.integration.test_dashboard_controls import stack as controls_stack
from tests.integration.test_models_and_budget import _card

from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.leader import LeaderOffice, Secretary
from trade_graph.application.leadership import GatewayRole
from trade_graph.application.scheduler import Scheduler
from trade_graph.contracts.models import ModelRequest
from trade_graph.domain.errors import AuthorityDenied


@pytest.fixture
def stack(tmp_path):
    return controls_stack.__wrapped__(tmp_path)


def permission_body(enabled=True, revision=0, request_id="paid-permission"):
    return {"request_id": request_id, "expected_revision": revision, "paid_calls_enabled": enabled}


def test_owner_permission_is_explicit_revisioned_idempotent_and_preserves_paper_money(stack):
    runtime, db = stack.runtime, stack.runtime.database
    cash = runtime.ledger.books(runtime.portfolio_id).cash
    allowance = db.execute("SELECT * FROM deployment_budget").fetchone()
    body = permission_body()
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=body)
    assert response.status_code == 200 and response.json()["policy"]["paid_calls_enabled"] is True
    assert response.json()["policy"]["live_enabled"] is False
    assert response.json()["policy"]["withdrawals_allowed"] is False
    assert stack.authority.active_policy().paid_calls_enabled is True
    assert runtime.ledger.books(runtime.portfolio_id).cash == cash
    assert dict(db.execute("SELECT * FROM deployment_budget").fetchone()) == dict(allowance)
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=body).json() == response.json()
    assert db.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == 2
    assert stack.client.post("/api/v1/owner/config", headers=stack.owner_headers,
                             json=permission_body(False)).status_code == 409
    # Removing the allowance cannot prevent the owner from revoking permission.
    db.execute("DELETE FROM deployment_budget")
    disabled = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers,
                                 json=permission_body(False, 1, "disable-paid"))
    assert disabled.status_code == 200 and disabled.json()["policy"]["paid_calls_enabled"] is False


@pytest.mark.parametrize("value", ["true", "false", "1", 1, 0, [], {}, None])
def test_paid_permission_requires_a_json_boolean_without_coercion(stack, value):
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=permission_body(value))
    assert response.status_code == 422
    assert stack.authority.active_policy().paid_calls_enabled is False
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == 0


@pytest.mark.parametrize("fault", [
    "missing", "total", "period", "daily", "root", "currency", "owner-total", "owner-daily", "owner-root",
    "owner-priority",
])
def test_large_virtual_balance_cannot_replace_positive_bounded_real_expense_allowances(stack, fault):
    db = stack.runtime.database
    assert stack.runtime.ledger.books(stack.runtime.portfolio_id).cash["USD"] == Decimal("10000")
    if fault == "missing":
        db.execute("DELETE FROM deployment_budget")
    elif fault == "currency":
        db.execute("UPDATE deployment_budget SET currency = 'USD'")
    elif fault.startswith("owner-"):
        column = {"owner-total": "total_allowance", "owner-daily": "daily_limit", "owner-root": "root_limit",
                  "owner-priority": "priority_reserve"}[fault]
        db.execute(f"UPDATE deployment_budget SET {column} = '50000'")
    else:
        column = {"total": "total_allowance", "period": "period_allowance",
                  "daily": "daily_limit", "root": "root_limit"}[fault]
        db.execute(f"UPDATE deployment_budget SET {column} = '0'")
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=permission_body())
    assert response.status_code == 403
    assert stack.authority.active_policy().paid_calls_enabled is False
    assert db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0] == 0


def test_only_owner_can_set_paid_permission_and_browser_writes_require_csrf(stack):
    body = permission_body()
    assert stack.client.post("/api/v1/owner/config", json=body).status_code == 401
    assert stack.client.post("/api/v1/owner/config", headers=stack.leader_headers, json=body).status_code == 403
    stack.client.cookies.set("tg_session", stack.owner)
    assert stack.client.post("/api/v1/owner/config", json=body).status_code == 403
    assert stack.authority.active_policy().paid_calls_enabled is False


def test_gateway_dispatch_reads_updated_owner_permission_before_reserving(stack):
    runtime, db = stack.runtime, stack.runtime.database
    budget = BudgetGateway(db, runtime.clock)
    budget.seed_card(_card().model_copy(update={"effective_at": "2026-01-01", "verified_at": "2026-01-01"}))
    scheduler = Scheduler(db, runtime.clock)
    office = LeaderOffice(runtime.execution, scheduler, budget)
    # This gateway has a separately selected runtime permission, but receives
    # only a local provider response fixture, never credentials or transport.
    gateway = ModelGateway(budget, paid_calls_enabled=True)
    handler = GatewayRole(office, Secretary(runtime.execution, scheduler), gateway,
                          deployment_id="deployment", price_card_id="card", provider="openai", model="gpt-6-luna")
    task_id = scheduler.add_task(role="research", objective="Synthetic permission check",
                                 portfolio_id=runtime.portfolio_id, allocated_spend=Decimal("0.1"))
    lease = scheduler.claim("permission-test")
    task = {**dict(scheduler.leased_row(lease)), "_lease": lease, "system_version_id": "v1"}
    request = ModelRequest(
        role="research", task_id=task_id, root_task_id=task_id, run_id="fixture", system_version_id="v1",
        provider="openai", model="gpt-6-luna", instructions="Synthetic provider fixture",
        context={"http_fixture": {
            "id": "synthetic-permission", "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }}, output_schema={"type": "object"}, schema_name="permission_fixture",
        max_output_tokens=10, max_tool_calls=0, timeout_seconds=1,
    )

    def invoke():
        return gateway.invoke(request, deployment_id="deployment", price_card_id="card",
                              fx_rate=Decimal("0.90"), fx_buffer=Decimal("1"),
                              authorize=lambda: handler._eligible(task, compare=False))

    with pytest.raises(AuthorityDenied, match="not enabled paid"):
        invoke()
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=permission_body())
    assert response.status_code == 200
    assert invoke().ok is True
    count = db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]
    assert count == 1
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers,
                                 json=permission_body(False, 1, "disable-paid"))
    assert response.status_code == 200
    with pytest.raises(AuthorityDenied, match="not enabled paid"):
        invoke()
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == count
