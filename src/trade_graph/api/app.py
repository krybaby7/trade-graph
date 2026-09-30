"""Server-rendered projections. Browser values are not a second ledger."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from trade_graph.api.auth import csrf_for_token, role_for_token
from trade_graph.application.activation import VersionController
from trade_graph.application.budget import BudgetGateway
from trade_graph.domain.errors import StaleState, ValidationFailure
from trade_graph.live_gate import evaluate_live_enablement

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "web" / "templates"))
_SECRET_MARKERS = ("OPENAI", "ANTHROPIC", "BEGIN PRIVATE", "sk-", "api_key")


def _redact(value: str) -> str:
    if any(marker.lower() in value.lower() for marker in _SECRET_MARKERS):
        return "[redacted]"
    return value


def _money(value: object, name: str) -> Decimal:
    if isinstance(value, float):
        raise HTTPException(status_code=422, detail="binary float is not a monetary amount")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=name) from exc
    if not parsed.is_finite():
        raise HTTPException(status_code=422, detail=name)
    return parsed


def create_app(runtime) -> FastAPI:
    app = FastAPI(title="Trade Graph")
    app.state.runtime = runtime

    def identity(request: Request) -> str:
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
        if token is None:
            token = request.cookies.get("tg_session")
        role = role_for_token(runtime.database, token)
        if role is None:
            raise HTTPException(status_code=401, detail="authentication required")
        request.state.token = token
        request.state.role = role
        return role

    def owner_write(request: Request) -> str:
        role = identity(request)
        if role != "owner":
            raise HTTPException(status_code=403, detail="owner authority required")
        if request.cookies.get("tg_session") and not request.headers.get("authorization"):
            expected = csrf_for_token(runtime.database, request.state.token)
            if request.headers.get("x-csrf-token") != expected:
                raise HTTPException(status_code=403, detail="csrf")
        return role

    def budget() -> BudgetGateway:
        return BudgetGateway(runtime.database, runtime.clock)

    def versions() -> VersionController:
        return VersionController(runtime.database, runtime.clock)

    @app.get("/api/v1/health")
    def health() -> dict:
        gate = evaluate_live_enablement(
            {"withdrawals_allowed": False, "paper_capital": "10000", "live_allocation": "0"}
        )
        return {
            "mode": "paper",
            "paid_calls_enabled": False,
            "live_enabled": False,
            "live_prerequisites": gate,
        }

    @app.get("/api/v1/overview")
    def overview(request: Request) -> dict:
        identity(request)
        equity = runtime.ledger.equity(runtime.portfolio_id)
        return {
            "portfolio_id": runtime.portfolio_id,
            "reporting_currency": equity.reporting_currency,
            "equity": None if equity.equity is None else str(equity.equity),
            "provisional": equity.provisional,
            "simulated": True,
            "actual_spend": str(runtime.actual_spend),
        }

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request) -> HTMLResponse:
        identity(request)
        equity = runtime.ledger.equity(runtime.portfolio_id)
        return TEMPLATES.TemplateResponse(
            request,
            "overview.html",
            {
                "equity": "provisional" if equity.equity is None else str(equity.equity),
                "currency": equity.reporting_currency,
                "provisional": equity.provisional,
                "spend": str(runtime.actual_spend),
            },
        )

    @app.get("/organization", response_class=HTMLResponse)
    def organization_page(request: Request) -> HTMLResponse:
        identity(request)
        return TEMPLATES.TemplateResponse(request, "organization.html", _organization(runtime))

    @app.get("/costs", response_class=HTMLResponse)
    def costs_page(request: Request) -> HTMLResponse:
        identity(request)
        return TEMPLATES.TemplateResponse(request, "costs.html", _costs(runtime))

    @app.get("/changes", response_class=HTMLResponse)
    def changes_page(request: Request) -> HTMLResponse:
        identity(request)
        return TEMPLATES.TemplateResponse(request, "changes.html", _changes(runtime))

    @app.get("/api/v1/tasks")
    def tasks(request: Request) -> dict:
        identity(request)
        return _organization(runtime)

    @app.get("/api/v1/orders")
    def orders(request: Request) -> dict:
        identity(request)
        rows = runtime.database.execute(
            "SELECT client_order_id, state FROM order_intents WHERE portfolio_id = ?",
            (runtime.portfolio_id,),
        ).fetchall()
        return {
            "orders": [
                {
                    "client_order_id": row["client_order_id"],
                    "state": row["state"],
                    "degraded": row["state"] in {"UNKNOWN", "SUBMITTING"},
                }
                for row in rows
            ],
            "simulated": True,
        }

    @app.get("/api/v1/costs")
    def costs(request: Request) -> dict:
        identity(request)
        return _costs(runtime)

    @app.get("/api/v1/changes")
    def changes(request: Request) -> dict:
        identity(request)
        return _changes(runtime)

    @app.post("/api/v1/owner/budgets")
    async def budgets(request: Request) -> dict:
        owner_write(request)
        body = await request.json()
        try:
            roles = {str(role): _money(amount, role) for role, amount in body["roles"].items()}
            budget().configure(
                deployment_id=str(body.get("deployment_id", "deployment")),
                currency="EUR",
                total=_money(body["total"], "total"),
                period=_money(body["period"], "period"),
                priority_reserve=_money(body["priority_reserve"], "priority_reserve"),
                daily=_money(body["daily"], "daily"),
                root=_money(body["root"], "root"),
                roles=roles,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid budget") from exc
        deployment_id = str(body.get("deployment_id", "deployment"))
        return {
            "deployment_id": deployment_id,
            "currency": "EUR",
            "total": str(budget().allowance(deployment_id)),
            "remaining": str(budget().remaining(deployment_id)),
            "simulated_equity_separate": True,
        }

    @app.post("/api/v1/owner/pause")
    async def pause(request: Request) -> dict:
        owner_write(request)
        body = await request.json()
        try:
            runtime.execution.set_pause(
                runtime.portfolio_id, body["profile"], "owner", body.get("reason", "owner")
            )
        except (KeyError, ValidationFailure) as exc:
            raise HTTPException(status_code=422, detail="invalid pause profile") from exc
        return {"profile": body["profile"], "originator": "owner"}

    @app.post("/api/v1/owner/enable-live")
    async def enable_live(request: Request) -> dict:
        owner_write(request)
        return {"enabled": False, "reason": "live activation is not implemented; this API is paper-only"}

    @app.post("/api/v1/owner/resume")
    async def resume(request: Request) -> dict:
        owner_write(request)
        await runtime.execution.reconcile()
        runtime.execution.set_pause(runtime.portfolio_id, "RUNNING", "owner", "resume after reconcile")
        return {"profile": runtime.execution.profile(runtime.portfolio_id), "reconciled": True}

    @app.post("/api/v1/leader/activate")
    async def leader_activate(request: Request) -> dict:
        role = identity(request)
        if role != "leader":
            raise HTTPException(status_code=403, detail="leader role required")
        body = await request.json()
        try:
            versions().activate(
                runtime.portfolio_id,
                {
                    "candidate_id": body["candidate_id"],
                    "baseline_hash": body["baseline_hash"],
                    "content_hash": body["content_hash"],
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail="candidate fields required") from exc
        except ValidationFailure as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StaleState as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"artifact_hash": versions().current_hash(runtime.portfolio_id)}

    return app


def _organization(runtime) -> dict:
    tasks = runtime.database.execute(
        """SELECT role, status, objective FROM tasks
        WHERE portfolio_id = ? OR portfolio_id IS NULL ORDER BY created_at""",
        (runtime.portfolio_id,),
    ).fetchall()
    active = runtime.database.execute(
        "SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?",
        (runtime.portfolio_id,),
    ).fetchone()
    return {
        "artifact_hash": None if active is None else active["artifact_hash"],
        "tasks": [
            {"role": row["role"], "status": row["status"], "objective": _redact(row["objective"])}
            for row in tasks
        ],
        "source": "task journal",
    }


def _costs(runtime) -> dict:
    uncertain = runtime.database.execute(
        "SELECT COUNT(*) AS n FROM budget_reservations WHERE state = 'UNCERTAIN'"
    ).fetchone()["n"]
    deployment = "deployment"
    configured = runtime.database.execute(
        "SELECT total_allowance FROM deployment_budget WHERE deployment_id = ?",
        (deployment,),
    ).fetchone()
    remaining = None
    if configured is not None:
        remaining = str(BudgetGateway(runtime.database, runtime.clock).remaining(deployment))
    return {
        "actual_spend": str(runtime.actual_spend),
        "currency": "EUR",
        "simulated": True,
        "simulated_trading_separate": True,
        "remaining_allowance": remaining,
        "uncertain_reservations": uncertain,
        "paper_equity_does_not_refill_allowance": True,
    }


def _changes(runtime) -> dict:
    candidates = runtime.database.execute(
        "SELECT candidate_id, state, content_hash FROM candidates ORDER BY created_at"
    ).fetchall()
    events = runtime.database.execute(
        """SELECT kind, from_hash, to_hash FROM version_events
        WHERE portfolio_id = ? ORDER BY created_at""",
        (runtime.portfolio_id,),
    ).fetchall()
    return {
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "state": row["state"],
                "content_hash": row["content_hash"],
            }
            for row in candidates
        ],
        "events": [
            {"kind": row["kind"], "from_hash": row["from_hash"], "to_hash": row["to_hash"]}
            for row in events
        ],
    }
