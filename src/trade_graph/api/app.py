"""Server-rendered projections. Browser values are not a second ledger."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from trade_graph.api.auth import csrf_for_token, role_for_token
from trade_graph.domain.errors import ValidationFailure

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "web" / "templates"))


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

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"mode": "paper", "paid_calls_enabled": False, "live_enabled": False}

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

    @app.get("/api/v1/costs")
    def costs(request: Request) -> dict:
        identity(request)
        return {"actual_spend": str(runtime.actual_spend), "simulated": True}

    @app.post("/api/v1/owner/budgets")
    async def budgets(request: Request) -> dict:
        owner_write(request)
        raise HTTPException(status_code=501, detail="budget persistence is not wired to this API")

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

    @app.post("/api/v1/leader/activate")
    async def leader_activate(request: Request) -> dict:
        role = identity(request)
        if role != "leader":
            raise HTTPException(status_code=403, detail="leader role required")
        raise HTTPException(status_code=501, detail="leader activation is not wired to this API")

    return app
