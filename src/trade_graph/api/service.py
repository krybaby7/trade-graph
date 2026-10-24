"""Authenticated owner lifecycle controls; serving the dashboard starts no worker."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trade_graph.api.security import redact
from trade_graph.application.service_controller import ServiceController
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


class StartCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    mode: Literal["paper", "live"] = "paper"


class CycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class StopCommand(CycleCommand):
    position_policy: Literal["manage-only", "flatten"] = "manage-only"


def controller(runtime) -> ServiceController:
    existing = getattr(runtime, "service_controller", None)
    if existing is None:
        existing = ServiceController(runtime)
        runtime.service_controller = existing
    return existing


async def _body(request, model):
    try:
        return model.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="Invalid service command.") from None


def _effect(callback):
    try:
        return redact(callback())
    except (AuthorityDenied, StaleState) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValidationFailure as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (OSError, RuntimeError):
        raise HTTPException(
            status_code=409, detail="Service control unavailable; inspect retained lifecycle status."
        ) from None


def register_service_controls(app, runtime, identity, owner_write):
    @app.get("/api/v1/service")
    def status(request: Request):
        identity(request)
        return _effect(lambda: controller(runtime).status())

    @app.post("/api/v1/owner/start-trading", status_code=202)
    async def start(request: Request):
        owner_write(request)
        body = await _body(request, StartCommand)
        return _effect(lambda: controller(runtime).start_trading(body.request_id, body.mode))

    @app.post("/api/v1/owner/start-optimisation", status_code=202)
    async def optimise(request: Request):
        owner_write(request)
        body = await _body(request, CycleCommand)
        return _effect(lambda: controller(runtime).start_optimisation(body.request_id))

    @app.post("/api/v1/owner/stop-service")
    async def stop(request: Request):
        owner_write(request)
        body = await _body(request, StopCommand)
        return _effect(lambda: controller(runtime).stop(body.request_id, body.position_policy))
