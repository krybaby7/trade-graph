"""Operator commands preserve authority, bounded commitments, and replay safety."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.auth import csrf_for_token, issue_session, role_for_token
from trade_graph.api.controls import register_controls
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import FillRecord, InstrumentRules
from trade_graph.domain.clock import FrozenClock


@pytest.fixture
def stack(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "controls.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "opening")
    execution = Execution(database, ledger, clock, PaperBroker(database, clock))
    authority = seed_paper_authority(database, clock, portfolio)
    execution.register_instrument(
        InstrumentRules(
            venue="paper",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            price_increment="0.1",
            quantity_increment="0.00000001",
            min_quantity="0.0001",
            min_notional="1",
            synthetic=True,
        )
    )
    BudgetGateway(database, clock).configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("5"),
        period=Decimal("5"),
        priority_reserve=Decimal("1"),
        daily=Decimal("1.50"),
        root=Decimal("1.50"),
        roles={"leader": Decimal("1"), "research": Decimal("2"), "learning": Decimal("2")},
    )
    runtime = SimpleNamespace(
        database=database,
        ledger=ledger,
        clock=clock,
        execution=execution,
        portfolio_id=portfolio,
        actual_spend=Decimal("0"),
    )
    app = FastAPI()

    # Exercise register_controls with the same identity/CSRF contract as create_app.
    def identity(request: Request):
        parts = request.headers.get("authorization", "").split()
        bearer = len(parts) == 2 and parts[0].lower() == "bearer"
        token = parts[1] if bearer else request.cookies.get("tg_session")
        role = role_for_token(database, token)
        if role is None:
            raise HTTPException(401, "authentication required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer:
            expected = csrf_for_token(database, token)
            if not expected or not secrets.compare_digest(expected, request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "csrf")
        return role

    def owner_write(request):
        role = identity(request)
        if role != "owner":
            raise HTTPException(403, "owner authority required")
        return role

    register_controls(app, runtime, identity, owner_write)
    owner, csrf = issue_session(database, clock, "owner")
    leader, _ = issue_session(database, clock, "leader")
    return SimpleNamespace(
        client=TestClient(app),
        runtime=runtime,
        authority=authority,
        owner=owner,
        csrf=csrf,
        owner_headers={"Authorization": f"Bearer {owner}"},
        leader_headers={"Authorization": f"Bearer {leader}"},
        identity=identity,
        owner_write=owner_write,
    )


def config_body(**updates):
    return {"request_id": "config-1", "expected_revision": 0, "maximum_gross_exposure_fraction": "0.60", **updates}


def test_role_caps_share_the_same_global_allowance(stack):
    fixture = stack
    response = fixture.client.post("/api/v1/owner/budgets", headers=fixture.owner_headers, json={
        "request_id": "overlapping-role-caps", "expected_revision": 0,
        "total": "5", "period": "5", "priority_reserve": "1", "daily": "5", "root": "2",
        "roles": {"leader": "3", "research": "3"},
    })
    assert response.status_code == 200
    assert response.json()["remaining"] == "5"
    assert response.json()["total"] == "5"
    # Individual role ceilings share one deployment pool; they are not extra funding.
    assert fixture.client.get("/api/v1/owner/config", headers=fixture.owner_headers).json()["budget"]["roles"] == {
        "leader": "3", "research": "3",
    }


def task_body(**updates):
    return {
        "request_id": "task-1",
        "expected_revision": 0,
        "role": "research",
        "objective": "Review fresh evidence",
        "allocated_spend": "1",
        **updates,
    }


def counts(stack):
    db = stack.runtime.database
    return (
        db.execute("SELECT COUNT(*) FROM dashboard_commands").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM dashboard_control_state").fetchone()[0],
    )


def test_config_is_persisted_runtime_authority_and_replay_is_side_effect_free(stack):
    client, headers = stack.client, stack.owner_headers
    initial = client.get("/api/v1/owner/config", headers=headers).json()
    assert initial["revision"] == 0
    assert initial["routing"]["supported"] is False
    response = client.post("/api/v1/owner/config", headers=headers, json=config_body())
    assert response.status_code == 200
    revised = stack.authority.active_policy()
    assert revised.maximum_gross_exposure_fraction == Decimal("0.60")
    assert revised.revision_id != "1"
    policy_count = stack.runtime.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0]
    replay = client.post("/api/v1/owner/config", headers=headers, json=config_body())
    assert replay.status_code == 200 and replay.json() == response.json()
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM owner_policy_revisions").fetchone()[0] == policy_count
    conflict = client.post(
        "/api/v1/owner/config", headers=headers, json=config_body(maximum_gross_exposure_fraction="0.70")
    )
    assert conflict.status_code == 409
    stale = client.post("/api/v1/owner/config", headers=headers, json=config_body(request_id="config-2"))
    assert stale.status_code == 409
    assert counts(stack) == (1, 1)
    read = client.get("/api/v1/owner/config", headers=headers).json()
    assert read["revision"] == 1 and read["policy"]["revision_id"] == revised.revision_id


@pytest.mark.parametrize(
    "updates",
    [
        {"live_enabled": True},
        {"withdrawals_allowed": True},
        {"database_path": "/tmp/untrusted.sqlite"},
        {"model_routing": {"provider": "unapproved"}},
        {"maximum_gross_exposure_fraction": 0.6},
        {"maximum_gross_exposure_fraction": "NaN"},
        {"maximum_gross_exposure_fraction": "invalid"},
        {"maximum_gross_exposure_fraction": "1.1"},
        {"expected_revision": False},
        {"maximum_quote_age_seconds": True},
        {"allowed_change_classes": ["executable_code"]},
        {"allowed_venues": ["unregistered"]},
    ],
)
def test_config_rejects_privilege_expansion_and_bad_types_without_mutations(stack, updates):
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=config_body(**updates))
    assert response.status_code in {403, 422}
    assert stack.authority.active_policy().revision_id == "1"
    assert counts(stack) == (0, 0)


def test_auth_and_cookie_csrf_precede_config_and_task_writes(stack):
    for endpoint, body in [("owner/config", config_body()), ("leader/tasks", task_body())]:
        assert stack.client.post(f"/api/v1/{endpoint}", json=body).status_code == 401
    assert stack.client.get("/api/v1/owner/config", headers=stack.leader_headers).status_code == 403
    assert (
        stack.client.post("/api/v1/owner/config", headers=stack.leader_headers, json=config_body()).status_code == 403
    )
    assert stack.client.post("/api/v1/leader/tasks", headers=stack.owner_headers, json=task_body()).status_code == 403
    stack.client.cookies.set("tg_session", stack.owner)
    assert stack.client.post("/api/v1/owner/config", json=config_body()).status_code == 403
    assert counts(stack) == (0, 0)
    response = stack.client.post("/api/v1/owner/config", headers={"X-CSRF-Token": stack.csrf}, json=config_body())
    assert response.status_code == 200


def test_tasks_are_bounded_persisted_assignments_and_replay_once(stack):
    body = task_body()
    first = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=body)
    assert first.status_code == 200
    replay = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=body)
    assert replay.json() == first.json()
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    task = stack.runtime.database.execute("SELECT * FROM tasks").fetchone()
    assert task["allocated_spend"] == "1"
    assert task["root_task_id"] == task["task_id"]
    second = stack.client.post(
        "/api/v1/leader/tasks",
        headers=stack.leader_headers,
        json=task_body(request_id="task-2", expected_revision=1, allocated_spend="1.1"),
    )
    assert second.status_code == 403  # Role's total commitment cannot be invented per root.
    assert counts(stack) == (1, 1)


@pytest.mark.parametrize(
    "updates",
    [
        {"role": "owner"},
        {"role": "engineer"},
        {"role": "unknown"},
        {"payload": {"paid_calls_enabled": True}},
        {"allocated_spend": "2"},
        {"allocated_spend": 0.1},
        {"allocated_spend": "Infinity"},
        {"allocated_spend": "bad"},
        {"max_attempts": 4},
        {"max_attempts": True},
        {"root_task_id": "nonexistent"},
        {"parent_id": "nonexistent"},
    ],
)
def test_invalid_or_unapproved_task_creates_no_records(stack, updates):
    result = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=task_body(**updates))
    assert result.status_code in {403, 422}
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert counts(stack) == (0, 0)


def test_child_cannot_switch_roots_or_oversubscribe_parent(stack):
    parent = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=task_body()).json()["task_id"]
    child = task_body(
        request_id="child-1",
        expected_revision=1,
        role="learning",
        parent_id=parent,
        root_task_id=parent,
        allocated_spend="0.8",
    )
    response = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=child)
    assert response.status_code == 200
    assert response.json()["root_task_id"] == parent
    exceeded = stack.client.post(
        "/api/v1/leader/tasks",
        headers=stack.leader_headers,
        json={**child, "request_id": "child-2", "expected_revision": 2, "allocated_spend": "0.3"},
    )
    assert exceeded.status_code == 403
    switched = stack.client.post(
        "/api/v1/leader/tasks",
        headers=stack.leader_headers,
        json={**child, "request_id": "child-3", "expected_revision": 2, "root_task_id": "other"},
    )
    assert switched.status_code == 403
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2


def test_nonflat_stop_requires_and_reports_management_policy(stack):
    runtime = stack.runtime
    runtime.ledger.apply_fill(
        runtime.portfolio_id,
        FillRecord(
            venue="paper",
            account_id="synthetic",
            trade_id="opening-position",
            intent_id=None,
            symbol="BTC/USD",
            side="buy",
            quantity="0.01",
            price="100",
            fee_amount="0",
            fee_asset="USD",
            liquidity="taker",
            filled_at_utc=runtime.clock.now(),
        ),
        base_asset="BTC",
        quote_asset="USD",
    )
    body = {"request_id": "stop-1", "expected_revision": 0, "profile": "STOPPED"}
    denied = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert denied.status_code == 422
    assert runtime.execution.profile(runtime.portfolio_id) == "RUNNING"
    assert counts(stack) == (0, 0)
    body["position_policy"] = "manage-only"
    result = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert result.status_code == 200
    assert result.json()["requested_profile"] == "STOPPED"
    assert result.json()["profile"] == "MANAGE_ONLY"
    assert result.json()["achieved"] == "managing"
    assert result.json()["management_continues"] is True
    replay = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body)
    assert replay.json() == result.json()
    resumed = stack.client.post(
        "/api/v1/owner/resume", headers=stack.owner_headers, json={"request_id": "resume-1", "expected_revision": 1}
    )
    assert resumed.status_code == 200  # Nonflat positions alone are not a resume barrier.


def test_resume_reconciles_outside_transaction_and_cannot_clear_unknown_order(stack):
    runtime = stack.runtime
    runtime.execution.set_pause(runtime.portfolio_id, "MANAGE_ONLY", "owner", "test")
    runtime.database.execute(
        """INSERT INTO order_intents
        (intent_id, portfolio_id, client_order_id, state, symbol, payload_json, created_at, updated_at)
        VALUES ('unknown', ?, 'unknown-client', 'UNKNOWN', 'BTC/USD',
        '{"client_order_id":"unknown-client","symbol":"BTC/USD"}', 'now', 'now')""",
        (runtime.portfolio_id,),
    )
    old = runtime.execution.reconcile
    calls = []

    async def checked():
        assert getattr(runtime.database._local, "connection", None) is None
        calls.append("reconcile")
        await old()

    runtime.execution.reconcile = checked
    body = {"request_id": "resume-1", "expected_revision": 0}
    response = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers, json=body)
    assert response.status_code == 409
    assert "unresolved orders" in response.json()["detail"]["barriers"]
    assert response.json()["detail"]["command_id"] == "resume-1"
    assert response.json()["detail"]["command_state"] == "FAILED"
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"
    assert calls == ["reconcile"]
    replay = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers, json=body)
    assert replay.status_code == 409 and replay.json() == response.json()
    assert calls == ["reconcile"]
    command = runtime.database.execute("SELECT status FROM dashboard_commands").fetchone()
    assert command["status"] == "FAILED:409"


def test_owner_resume_does_not_clear_system_or_incomplete_pause(stack):
    runtime = stack.runtime
    runtime.execution.set_pause(runtime.portfolio_id, "MANAGE_ONLY", "system", "controller recovery")
    response = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers)
    assert response.status_code == 409
    assert runtime.execution.pause(runtime.portfolio_id)["originator"] == "system"
    runtime.execution.set_pause(runtime.portfolio_id, "FLATTEN", "owner", "flatten")
    response = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers)
    assert response.status_code == 409
    assert runtime.execution.profile(runtime.portfolio_id) == "FLATTEN"


def test_pending_command_prevents_racing_writes_and_duplicate_reconciliation(stack):
    runtime = stack.runtime
    calls = []

    async def checked():
        commands = runtime.database.execute("SELECT * FROM dashboard_commands").fetchall()
        assert commands[0]["status"] == "PROCESSING"
        assert getattr(runtime.database._local, "connection", None) is None
        # A separate connection/request sees the durably reserved revision and command lock.
        denied = stack.client.post(
            "/api/v1/owner/config",
            headers=stack.owner_headers,
            json=config_body(request_id="racing", expected_revision=1),
        )
        assert denied.status_code == 409
        calls.append("reconciled")

    runtime.execution.reconcile = checked
    body = {"request_id": "resume-1", "expected_revision": 0}
    result = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers, json=body)
    assert result.status_code == 200 and calls == ["reconciled"]
    replay = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers, json=body)
    assert replay.json() == result.json() and calls == ["reconciled"]


def test_owner_budget_revises_consumed_financial_authority_but_keeps_paid_live_disabled(stack):
    body = {
        "request_id": "budget-1",
        "expected_revision": 0,
        "total": "9",
        "period": "9",
        "priority_reserve": "1",
        "daily": "2",
        "root": "2",
        "roles": {"leader": "1", "research": "3"},
    }
    result = stack.client.post("/api/v1/owner/budgets", headers=stack.owner_headers, json=body)
    assert result.status_code == 200
    policy = stack.authority.active_policy()
    assert policy.monthly_operating.amount == Decimal("9") and policy.root_paid_limit.amount == Decimal("2")
    assert policy.paid_calls_enabled is False and policy.live_enabled is False and policy.withdrawals_allowed is False
    replay = stack.client.post("/api/v1/owner/budgets", headers=stack.owner_headers, json=body)
    assert replay.json() == result.json()
    live = stack.client.post(
        "/api/v1/owner/enable-live",
        headers=stack.owner_headers,
        json={"request_id": "live-1", "expected_revision": 1, "live_allocation": "25"},
    )
    assert live.status_code == 200 and live.json()["enabled"] is False


def test_new_commands_require_ids_and_revisions_and_legacy_controls_still_work(stack):
    assert (
        stack.client.post(
            "/api/v1/owner/config", headers=stack.owner_headers, json={"allowed_symbols": ["BTC/USD"]}
        ).status_code
        == 422
    )
    assert (
        stack.client.post(
            "/api/v1/leader/tasks",
            headers=stack.leader_headers,
            json={"role": "research", "objective": "review", "allocated_spend": "0"},
        ).status_code
        == 422
    )
    assert (
        stack.client.post(
            "/api/v1/owner/pause", headers=stack.owner_headers, json={"profile": "MANAGE_ONLY"}
        ).status_code
        == 200
    )
    assert stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers).status_code == 200
    assert (
        stack.client.post(
            "/api/v1/owner/enable-live",
            headers=stack.owner_headers,
            json={"paper_capital": "10000", "live_allocation": "0"},
        ).status_code
        == 200
    )
    assert (
        stack.client.post(
            "/api/v1/leader/activate",
            headers=stack.leader_headers,
            json={
                "candidate_id": "fake",
                "baseline_hash": "a",
                "content_hash": "b",
                "attestation": {"runner": "caller"},
            },
        ).status_code
        == 422
    )


def test_same_role_children_share_root_commitment_without_double_counting(stack):
    parent = stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=task_body()).json()["task_id"]
    child = task_body(request_id="child", expected_revision=1, parent_id=parent, root_task_id=parent)
    assert stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=child).status_code == 200
    other = task_body(request_id="other-root", expected_revision=2)
    assert stack.client.post("/api/v1/leader/tasks", headers=stack.leader_headers, json=other).status_code == 200
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3


def test_owner_pause_cannot_erase_system_resume_barrier(stack):
    runtime = stack.runtime
    runtime.execution.set_pause(runtime.portfolio_id, "MANAGE_ONLY", "system", "controller recovery")
    response = stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json={"profile": "MANAGE_ONLY"})
    assert response.status_code == 409
    assert runtime.execution.pause(runtime.portfolio_id)["originator"] == "system"
    assert counts(stack) == (0, 0)
    assert stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers).status_code == 409


def test_unregistered_native_holdings_cannot_be_reported_flat_or_resume_after_flatten(stack):
    runtime = stack.runtime
    runtime.ledger.deposit(runtime.portfolio_id, "DOGE", Decimal("100"), "native-opening")
    body = {"profile": "STOPPED"}
    assert stack.client.post("/api/v1/owner/pause", headers=stack.owner_headers, json=body).status_code == 422
    response = stack.client.post(
        "/api/v1/owner/pause", headers=stack.owner_headers, json={**body, "position_policy": "flatten"}
    )
    assert response.status_code == 200
    assert response.json()["achieved"] == "unresolved-native-holdings"
    assert runtime.execution.pause(runtime.portfolio_id)["achieved"] == "unresolved-native-holdings"
    assert stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers).status_code == 409


@pytest.mark.parametrize("barrier", ["billing", "invoice", "version"])
def test_unresolved_billing_invoice_and_version_barriers_keep_owner_halt(stack, barrier):
    runtime = stack.runtime
    runtime.execution.set_pause(runtime.portfolio_id, "MANAGE_ONLY", "owner", "test")
    if barrier == "billing":
        runtime.database.execute(
            """INSERT INTO budget_reservations
            (reservation_id, deployment_id, role, amount, currency, state, price_card_id, purpose,
             synthetic, created_at, updated_at)
            VALUES ('uncertain', 'deployment', 'leader', '1', 'EUR', 'UNCERTAIN', 'fixture', 'test', 0, 'now', 'now')"""
        )
    elif barrier == "invoice":
        BudgetGateway(runtime.database, runtime.clock).reconcile_invoice(
            deployment_id="deployment",
            invoice_id="fixture",
            invoice_total=Decimal("1"),
        )
    else:
        runtime.database.execute(
            """INSERT INTO version_rollouts VALUES
            ('rollout', ?, 'candidate', 1, 'target', 'previous', 'previous-version', 'RESTART_PENDING', '{}', 'now')""",
            (runtime.portfolio_id,),
        )
    response = stack.client.post("/api/v1/owner/resume", headers=stack.owner_headers)
    assert response.status_code == 409
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"


def test_pending_commands_are_exposed_to_owner_without_request_contents(stack):
    runtime = stack.runtime
    runtime.database.execute(
        "INSERT INTO dashboard_commands VALUES ('interrupted', ?, 'fixture-hash', NULL, 'PROCESSING', 'now')",
        ("owner:deployment",),
    )
    response = stack.client.get("/api/v1/owner/config", headers=stack.owner_headers)
    assert response.status_code == 200
    assert response.json()["pending_commands"] == [
        {"command_id": "interrupted", "status": "PROCESSING", "created_at": "now"}
    ]
    assert "request_hash" not in response.text and "response_json" not in response.text


def test_shared_owner_policy_uses_global_revision_and_request_replay_is_portfolio_bound(stack):
    runtime = stack.runtime
    other_portfolio = runtime.ledger.create_portfolio(reporting_currency="EUR")
    other = SimpleNamespace(**{**vars(runtime), "portfolio_id": other_portfolio})
    app = FastAPI()
    register_controls(app, other, stack.identity, stack.owner_write)
    other_client = TestClient(app)
    response = stack.client.post("/api/v1/owner/config", headers=stack.owner_headers, json=config_body())
    assert response.status_code == 200
    read = other_client.get("/api/v1/owner/config", headers=stack.owner_headers).json()
    assert read["scope"] == "owner:deployment" and read["revision"] == 1
    stale = other_client.post(
        "/api/v1/owner/config",
        headers=stack.owner_headers,
        json=config_body(request_id="other-config", maximum_gross_exposure_fraction="0.70"),
    )
    assert stale.status_code == 409
    replay = other_client.post("/api/v1/owner/config", headers=stack.owner_headers, json=config_body())
    assert replay.status_code == 409  # This request identity belongs to another runtime portfolio.
    assert stack.authority.active_policy().maximum_gross_exposure_fraction == Decimal("0.60")
    assert counts(stack) == (1, 1)


def test_budget_cannot_configure_a_different_deployment(stack):
    body = {
        "deployment_id": "other",
        "total": "5",
        "period": "5",
        "priority_reserve": "1",
        "daily": "1",
        "root": "1",
        "roles": {"leader": "1"},
    }
    response = stack.client.post("/api/v1/owner/budgets", headers=stack.owner_headers, json=body)
    assert response.status_code == 403
    assert counts(stack) == (0, 0)
    assert stack.runtime.database.execute("SELECT COUNT(*) FROM deployment_budget").fetchone()[0] == 1


def test_interrupted_resume_latches_manage_only_and_safe_emergency_remains_available(stack):
    import asyncio

    from starlette.requests import Request

    runtime = stack.runtime

    async def cancelled():
        assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"
        assert getattr(runtime.database._local, "connection", None) is None
        raise asyncio.CancelledError

    runtime.execution.reconcile = cancelled
    # Call the route coroutine directly to simulate process cancellation without
    # an HTTP test client's transport converting it into a connection failure.
    route = next(route for route in stack.client.app.routes if route.path == "/api/v1/owner/resume")
    body = json_bytes({"request_id": "interrupted", "expected_revision": 0})

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/owner/resume",
            "headers": [(b"authorization", f"Bearer {stack.owner}".encode())],
        },
        receive,
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(route.endpoint(request))
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"
    pending = stack.client.get("/api/v1/owner/config", headers=stack.owner_headers).json()
    assert pending["revision"] == 1 and pending["pending_commands"][0]["command_id"] == "interrupted"
    emergency = stack.client.post(
        "/api/v1/owner/pause",
        headers=stack.owner_headers,
        json={"request_id": "emergency", "expected_revision": 1, "profile": "MANAGE_ONLY"},
    )
    assert emergency.status_code == 200
    assert emergency.json()["revision"] == 2 and emergency.json()["reconciliation_required"] is True
    assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"
    assert stack.client.get("/api/v1/owner/config", headers=stack.owner_headers).json()["pending_commands"]
    blocked = stack.client.post(
        "/api/v1/owner/config",
        headers=stack.owner_headers,
        json=config_body(request_id="config-after-crash", expected_revision=2),
    )
    assert blocked.status_code == 409
    replay = stack.client.post(
        "/api/v1/owner/pause",
        headers=stack.owner_headers,
        json={"request_id": "emergency", "expected_revision": 1, "profile": "MANAGE_ONLY"},
    )
    assert replay.json() == emergency.json()


def json_bytes(body):
    import json

    return json.dumps(body).encode()


def test_delayed_resume_cannot_lift_newer_emergency_owner_latch(stack):
    runtime = stack.runtime
    calls = []

    async def delayed():
        assert runtime.execution.profile(runtime.portfolio_id) == "MANAGE_ONLY"
        calls.append("reconcile")
        emergency = stack.client.post(
            "/api/v1/owner/pause",
            headers=stack.owner_headers,
            json={
                "request_id": "emergency",
                "expected_revision": 1,
                "profile": "MANAGE_ONLY",
                "reason": "newer owner halt",
            },
        )
        assert emergency.status_code == 200 and emergency.json()["revision"] == 2

    runtime.execution.reconcile = delayed
    response = stack.client.post(
        "/api/v1/owner/resume", headers=stack.owner_headers, json={"request_id": "slow-resume", "expected_revision": 0}
    )
    assert response.status_code == 409
    assert "owner state changed" in " ".join(response.json()["detail"]["barriers"])
    pause = runtime.execution.pause(runtime.portfolio_id)
    assert pause["profile"] == "MANAGE_ONLY" and pause["reason"] == "newer owner halt"
    assert calls == ["reconcile"]
    assert stack.client.get("/api/v1/owner/config", headers=stack.owner_headers).json()["pending_commands"] == []
    replay = stack.client.post(
        "/api/v1/owner/resume", headers=stack.owner_headers, json={"request_id": "slow-resume", "expected_revision": 0}
    )
    assert replay.status_code == 409 and calls == ["reconcile"]


def test_legacy_attestation_label_is_ignored_and_trusted_controller_proof_remains_required(tmp_path):
    from tests.integration.test_activation import _candidate, _ready

    database, portfolio, versions, baseline, result = _ready(tmp_path)
    runtime = SimpleNamespace(database=database, clock=versions.clock, portfolio_id=portfolio)
    app = FastAPI()
    register_controls(app, runtime, lambda request: "leader", lambda request: "owner")
    client = TestClient(app)
    body = {**_candidate(result, baseline), "request_id": "activate", "expected_revision": 0}
    body["attestation"] = {"runner": "ignored-untrusted-label"}
    response = client.post("/api/v1/leader/activate", json=body)
    assert response.status_code == 200 and response.json()["artifact_hash"] == result.content_hash
    # Replays are bound to protected candidate identity, independent of the legacy label.
    body["attestation"] = {"runner": "changed-label"}
    assert client.post("/api/v1/leader/activate", json=body).json() == response.json()
    untrusted = client.post(
        "/api/v1/leader/activate",
        json={
            "candidate_id": "unknown",
            "baseline_hash": result.content_hash,
            "content_hash": "fake",
            "request_id": "untrusted",
            "expected_revision": 1,
            "attestation": {"runner": "trusted-controller"},
        },
    )
    assert untrusted.status_code == 422
