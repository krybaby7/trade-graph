"""Authenticated dashboard over authoritative read snapshots."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trade_graph.api import evidence, financial, progress, progress_runs
from trade_graph.api.auth import csrf_for_token, role_for_token
from trade_graph.api.controls import configuration, register_controls
from trade_graph.api.health import health as project_health
from trade_graph.api.security import redact
from trade_graph.api.service import controller as service_controller
from trade_graph.api.service import register_service_controls
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import NotFound

WEB = Path(__file__).resolve().parents[1] / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB / "templates"))
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1000000)]


def create_app(runtime) -> FastAPI:
    app = FastAPI(title="Trade Graph", docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    # Construction starts no scheduler, provider, broker or background worker.
    if (WEB / "static").exists():
        app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def identity(request: Request) -> str:
        parts = request.headers.get("authorization", "").split()
        bearer = len(parts) == 2 and parts[0].lower() == "bearer"
        token = parts[1] if bearer else request.cookies.get("tg_session")
        role = role_for_token(runtime.database, token)
        if role is None:
            raise HTTPException(status_code=401, detail="authentication required")
        request.state.token, request.state.role = token, role
        request.state.auth_mechanism = "bearer" if bearer else "cookie"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer:
            expected = csrf_for_token(runtime.database, token)
            provided = request.headers.get("x-csrf-token", "")
            if not expected or not secrets.compare_digest(provided, expected):
                raise HTTPException(status_code=403, detail="csrf")
        return role

    def owner_write(request: Request) -> str:
        role = identity(request)
        if role != "owner":
            raise HTTPException(status_code=403, detail="owner authority required")
        return role

    @app.exception_handler(NotFound)
    async def inaccessible_record(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": "record not found"})

    @app.exception_handler(RequestValidationError)
    async def invalid_parameters(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "invalid request parameters"})

    def render(request: Request, name: str, data: dict):
        csrf = csrf_for_token(runtime.database, request.state.token) if request.state.auth_mechanism == "cookie" else ""
        portfolio = runtime.database.execute(
            "SELECT mode FROM portfolios WHERE portfolio_id = ?", (runtime.portfolio_id,)
        ).fetchone()
        operating_mode = "live" if portfolio and portfolio["mode"] == "live" else "paper"
        return TEMPLATES.TemplateResponse(request, f"{name}.html", {
            "data": redact(data), "role": request.state.role, "csrf": csrf, "page": name,
            "auth_mechanism": request.state.auth_mechanism, "operating_mode": operating_mode,
        })

    def overview_data():
        data = financial.overview(runtime)
        data["health"] = project_health(runtime, authenticated=True)
        data["degraded"] = data["health"]["degraded"]
        data["degraded_reasons"] = data["health"]["degraded_reasons"]
        return data

    def progress_data():
        storage_error = None
        try:
            test_runs = progress_runs.runs(runtime)
        except RuntimeError:
            test_runs = []
            storage_error = "Test history is unavailable. Check the private runtime folder before running tests."
        data = progress.progress(runtime, test_runs=test_runs)
        checks = progress_runs.checks()
        if storage_error:
            checks = [{**check, "available": False, "reason": storage_error} for check in checks]
        data["checks"] = checks
        data["test_storage_error"] = storage_error
        data["lifecycle"] = service_controller(runtime).status()
        return data

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {
            "data": {}, "role": "reader", "page": "login", "csrf": "", "auth_mechanism": "none",
        })

    @app.post("/api/v1/session")
    async def login(request: Request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="same-origin login required")
        try:
            body = await request.json()
            token = body.get("session_token") if isinstance(body, dict) else None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid session") from exc
        if not isinstance(token, str) or len(token) > 256 or role_for_token(runtime.database, token) is None:
            raise HTTPException(status_code=401, detail="invalid session")
        response = JSONResponse({"authenticated": True})
        response.set_cookie("tg_session", token, httponly=True, secure=request.url.scheme == "https",
                            samesite="strict", path="/")
        return response

    @app.post("/api/v1/logout")
    def logout(request: Request):
        identity(request)
        runtime.database.execute("DELETE FROM sessions WHERE token_hash = ?",
                                 (hashlib.sha256(request.state.token.encode()).hexdigest(),))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("tg_session", path="/")
        return response

    @app.get("/api/v1/health")
    def health(request: Request):
        authenticated = bool(request.headers.get("authorization") or request.cookies.get("tg_session"))
        if authenticated:
            identity(request)
        with runtime.database.snapshot():
            return project_health(runtime, authenticated=authenticated)

    @app.get("/api/v1/overview")
    def overview(request: Request):
        identity(request)
        with runtime.database.snapshot():
            return redact(overview_data())

    @app.get("/api/v1/progress")
    def progress_projection(request: Request):
        identity(request)
        with runtime.database.snapshot():
            return redact(progress_data())

    @app.post("/api/v1/progress/tests/{check_id}/run", status_code=202)
    def start_progress_test(request: Request, check_id: str):
        owner_write(request)
        try:
            return redact(progress_runs.start(runtime, check_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="test is unavailable or unknown") from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409, detail="another test is active or test storage is unavailable"
            ) from exc

    @app.get("/api/v1/positions")
    def positions(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(financial.positions(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/orders")
    def orders(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(financial.orders(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/costs")
    def costs(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(financial.costs(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/tasks")
    def tasks(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.organization(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/decisions")
    def decisions(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.decisions(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/decisions/{decision_id}")
    def decision(request: Request, decision_id: str):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.decision(runtime, decision_id))

    @app.get("/api/v1/research")
    def research(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.research(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/lessons")
    def lessons(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.lessons(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/changes")
    def changes(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.changes(runtime, limit=limit, offset=offset))

    @app.get("/api/v1/changes/{candidate_id}")
    def change(request: Request, candidate_id: str):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.change(runtime, candidate_id))

    @app.get("/api/v1/events")
    def events(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return redact(evidence.events(runtime, limit=limit, offset=offset))

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "overview", overview_data())

    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "progress", progress_data())

    @app.get("/trading", response_class=HTMLResponse)
    def trading_page(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            positions = financial.positions(runtime, limit=limit, offset=offset)
            orders = financial.orders(runtime, limit=limit, offset=offset)
            data = {**positions, **orders, "positions_pagination": positions["pagination"],
                    "orders_pagination": orders["pagination"]}
            data["health"] = project_health(runtime, authenticated=True)
            return render(request, "trading", data)

    @app.get("/organization", response_class=HTMLResponse)
    def organization_page(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            data = evidence.organization(runtime, limit=limit, offset=offset)
            data["research"] = evidence.research(runtime, limit=limit, offset=offset)
            data["lessons"] = evidence.lessons(runtime, limit=limit, offset=offset)
            data["events"] = evidence.events(runtime, limit=limit, offset=offset).get("events", [])
            return render(request, "organization", data)

    @app.get("/costs", response_class=HTMLResponse)
    def costs_page(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "costs", financial.costs(runtime, limit=limit, offset=offset))

    @app.get("/changes", response_class=HTMLResponse)
    def changes_page(request: Request, limit: Limit = 50, offset: Offset = 0):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "changes", evidence.changes(runtime, limit=limit, offset=offset))

    @app.get("/decisions/{decision_id}", response_class=HTMLResponse)
    def decision_page(request: Request, decision_id: str):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "evidence", {"kind": "decision", **evidence.decision(runtime, decision_id)})

    @app.get("/changes/{candidate_id}", response_class=HTMLResponse)
    def change_page(request: Request, candidate_id: str):
        identity(request)
        with runtime.database.snapshot():
            return render(request, "evidence", {"kind": "change", **evidence.change(runtime, candidate_id)})

    @app.get("/owner", response_class=HTMLResponse)
    def owner_page(request: Request):
        owner_write(request)
        with runtime.database.snapshot():
            data = configuration(runtime)
            data["health"] = project_health(runtime, authenticated=True)
            data["live_prerequisites"] = data["health"]["live_prerequisites"]
            data["lifecycle"] = service_controller(runtime).status()
            return render(request, "owner", data)

    register_controls(app, runtime, identity, owner_write)
    register_service_controls(app, runtime, identity, owner_write)
    return app


def _changes(runtime) -> dict:
    """Compatibility for existing scoped change-projection consumers."""
    with runtime.database.snapshot():
        projection_runtime = SimpleNamespace(database=runtime.database, portfolio_id=runtime.portfolio_id,
                                             clock=getattr(runtime, "clock", SystemClock()))
        return evidence.changes(projection_runtime)
