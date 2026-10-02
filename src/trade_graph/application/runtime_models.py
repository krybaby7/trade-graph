"""Protected model setup and real, versioned six-role runtime assembly.

Routing artifacts contain only immutable price-card references. Prices, provider
capabilities, funding authority and credentials come from protected software/owner
configuration. No model receives the credential map, transport or configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from trade_graph.adapters.models.providers import lookup_capabilities
from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.application.engineering_workflow import EngineerHandler
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.leadership import LeaderHandler
from trade_graph.application.model_invocations import InvocationJournal
from trade_graph.application.runtime_departments import LearningHandler, OptimisationHandler, ResearchHandler
from trade_graph.application.trader_workflow import TraderHandler
from trade_graph.contracts.models import ContractModel, ModelRequest, ModelResult
from trade_graph.domain.errors import AuthorityDenied, TradeGraphError, ValidationFailure

ModelRole = Literal["research", "trader", "learning", "optimisation", "leader", "engineer"]
MODEL_ROLES = ("research", "trader", "learning", "optimisation", "leader", "engineer")


class RuntimeModelConfig(ContractModel):
    deployment_id: str = Field(default="deployment", min_length=1, max_length=128)
    paid_calls_enabled: bool = False
    approved_price_card_ids: list[str] = Field(default_factory=list, max_length=32)
    role_routes: dict[ModelRole, str] = Field(default_factory=dict)
    fx_rate: Decimal = Decimal("1")
    fx_buffer: Decimal = Decimal("1.02")
    fx_rate_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("fx_rate", "fx_buffer")
    @classmethod
    def positive_fx(cls, value: Decimal, info) -> Decimal:
        if not value.is_finite() or value <= 0 or (info.field_name == "fx_buffer" and value < 1):
            raise ValueError("positive FX rate and conservative buffer required")
        return value


class ModelRouter:
    def __init__(self, budget, artifact_runtime, config: RuntimeModelConfig, *, api_keys=None) -> None:
        self.budget, self.artifact_runtime = budget, artifact_runtime
        # Detached copies prevent a caller mutating approval during a dispatch.
        self.config = config.model_copy(deep=True)
        self._available_providers = frozenset(k for k, value in (api_keys or {}).items() if value)

    def card_route(self, card_id: str, *, require_available: bool = True) -> dict:
        if card_id not in self.config.approved_price_card_ids:
            raise AuthorityDenied("model route is outside the protected approved price-card registry")
        try:
            card = self.budget.card(card_id)
        except TradeGraphError as exc:
            raise ValidationFailure("approved model route has no persisted price card") from exc
        caps = lookup_capabilities(card.provider, card.model)
        if caps is None or not caps.structured_output:
            raise ValidationFailure("model route lacks an approved structured-output capability")
        if card.provider != "scripted":
            if not card.verified_at or not card.source_id or card.tier != "standard":
                raise ValidationFailure("model route requires a verified standard-tier price card")
            try:
                verified, effective = date.fromisoformat(card.verified_at), date.fromisoformat(card.effective_at)
            except ValueError as exc:
                raise ValidationFailure("model route price dates are invalid") from exc
            today = self.budget.clock.now().date()
            if verified > today or effective > today or (today - verified).days > 30:
                raise ValidationFailure("model route price verification is stale or not yet effective")
            if require_available:
                if not self.config.paid_calls_enabled:
                    raise AuthorityDenied("runtime paid calls are disabled")
                if card.provider not in self._available_providers:
                    raise AuthorityDenied("model route has no configured private provider credential")
        return {"price_card_id": card.price_card_id, "provider": card.provider, "model": card.model}

    def resolve(self, task: dict, *, require_available: bool = True) -> dict:
        bundle = self.artifact_runtime.bundle_for(task)
        artifact_routes = self.artifact_runtime.model_routes(bundle)
        card_id = artifact_routes.get(task["role"], self.config.role_routes.get(task["role"]))
        if not card_id:
            raise ValidationFailure("role has no configured model route")
        return {
            **self.card_route(card_id, require_available=require_available),
            "source": "artifacts/model_routing.json" if task["role"] in artifact_routes else "owner-runtime-config",
            "artifact": self.artifact_runtime.identity(bundle),
        }

    def validate_bundle(self, bundle: dict) -> None:
        for card_id in self.artifact_runtime.model_routes(bundle).values():
            self.card_route(card_id, require_available=False)

    def readiness(self, portfolio_id: str | None = None) -> dict:
        """Read-only, secret-free preflight suitable for operator diagnostics."""
        bundle = self.artifact_runtime.versions.load_active(portfolio_id) if portfolio_id else None
        overrides = self.artifact_runtime.model_routes(bundle) if bundle else {}
        roles = {}
        for role in MODEL_ROLES:
            card_id = overrides.get(role, self.config.role_routes.get(role))
            try:
                if not card_id:
                    raise ValidationFailure("role has no configured model route")
                route = self.card_route(card_id)
                roles[role] = {**route, "ready": True}
            except TradeGraphError as exc:
                roles[role] = {"price_card_id": card_id, "ready": False, "reason": str(exc)}
        return {"paid_calls_enabled": self.config.paid_calls_enabled, "roles": roles,
                "ready": all(item["ready"] for item in roles.values())}


class RuntimeGateway(ModelGateway):
    """Every runtime dispatch rechecks the protected route before reserving."""

    def __init__(self, *args, router: ModelRouter, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.router = router

    def invoke(self, request: ModelRequest, **kwargs) -> ModelResult:
        route = self.router.card_route(kwargs["price_card_id"])
        if route["provider"] != request.provider or route["model"] != request.model:
            raise AuthorityDenied("invocation does not match protected model route")
        if request.synthetic != (request.provider == "scripted"):
            raise AuthorityDenied("runtime cannot relabel paid provider work as synthetic")
        if request.provider != "scripted" and "http_fixture" in request.context:
            raise AuthorityDenied("runtime task cannot supply provider fixtures")
        configured_id = self.router.config.fx_rate_id
        if configured_id is not None and self.budget.card(kwargs["price_card_id"]).currency != "EUR":
            if "fx_rate_id" in kwargs and kwargs["fx_rate_id"] != configured_id:
                raise AuthorityDenied("invocation FX source differs from protected runtime configuration")
            kwargs["fx_rate_id"] = configured_id
        return super().invoke(request, **kwargs)

    def _usage_is_priced(self, request: ModelRequest, result: ModelResult) -> bool:
        return result.provider_model is None or result.provider_model == request.model


class DurableDepartmentMixin:
    """One durable inference per ordinary task, including Leader and consultations."""

    def _invocation_id(self, task: dict) -> str:
        return f"{task['role']}:{task['task_id']}"

    def invoke(self, task: dict, request: ModelRequest):
        result = self.gateway.invoke(
            request, deployment_id=self.deployment_id, price_card_id=self.price_card_id,
            fx_rate=self.gateway.router.config.fx_rate, fx_buffer=self.gateway.router.config.fx_buffer,
            priority=task["role"] == "leader", invocation_id=self._invocation_id(task),
            portfolio_id=task["portfolio_id"], authorize=lambda: self._eligible(task),
        )
        row = self.database.execute("SELECT state FROM model_invocations WHERE invocation_id = ?",
                                    (self._invocation_id(task),)).fetchone()
        if row and row["state"] == "UNCERTAIN":
            raise ValidationFailure("Model outcome/billing requires reconciliation")
        return result

    def recover(self, task: dict) -> dict | None:
        completed = super().recover(task)
        if completed is not None:
            return completed
        with self.database.immediate():
            self.scheduler.leased_row(task["_lease"])
            row = self.database.execute("SELECT * FROM model_invocations WHERE invocation_id = ? AND task_id = ?",
                                        (self._invocation_id(task), task["task_id"])).fetchone()
            if row is None:
                return None
            result = InvocationJournal(self.gateway.budget).recover(row["invocation_id"], row["request_hash"])
            snapshot = self.database.execute("SELECT payload_json FROM snapshots WHERE snapshot_id = ?",
                                             (row["run_id"],)).fetchone()
            if snapshot is None:
                return self._record(task, {"_status": "FAILED", "reason": "durable role snapshot is missing"})
            task.update(snapshot=json.loads(snapshot[0]), snapshot_id=row["run_id"],
                        system_version_id=row["system_version_id"])
            if row["state"] == "UNCERTAIN" or result.failure == "timeout_uncertain":
                return self._record(task, {"_status": "WAITING_EXTERNAL",
                                           "reason": "Model outcome/billing requires reconciliation"})
        try:
            if not result.ok:
                raise ValidationFailure(f"model {result.failure}: {result.message}")
            return self.apply(task, self.reply_type.model_validate(result.payload))
        except (TradeGraphError, ValidationError) as exc:
            with self.database.immediate():
                self.scheduler.leased_row(task["_lease"])
                return self._record(task, {"_status": "FAILED", "reason": str(exc)[:500]})

    def __call__(self, task: dict) -> dict:
        output = super().__call__(task)
        row = self.database.execute("SELECT state FROM model_invocations WHERE invocation_id = ?",
                                    (self._invocation_id(task),)).fetchone()
        if row and row["state"] == "UNCERTAIN":
            with self.database.immediate():
                self.scheduler.leased_row(task["_lease"])
                output["_status"] = "WAITING_EXTERNAL"
                self.database.execute("UPDATE role_results SET status = ?, document_json = ? WHERE task_id = ?",
                                      ("WAITING_EXTERNAL", json.dumps(output, sort_keys=True), task["task_id"]))
        return output


class RuntimeResearchHandler(DurableDepartmentMixin, ResearchHandler):
    pass


class RuntimeLearningHandler(DurableDepartmentMixin, LearningHandler):
    pass


class RuntimeOptimisationHandler(DurableDepartmentMixin, OptimisationHandler):
    pass


class RuntimeLeaderHandler(DurableDepartmentMixin, LeaderHandler):
    def _action(self, task: dict, decision_id: str, action, evidence_refs: list[str]) -> dict:
        if action.kind == "activate":
            proof = self.database.execute("SELECT report_json FROM candidate_attestations WHERE candidate_id = ?",
                                          (action.candidate_id,)).fetchone()
            if proof:
                self.gateway.router.validate_bundle({"files": json.loads(proof[0])["artifact_files"]})
        return super()._action(task, decision_id, action, evidence_refs)


class RuntimeTraderHandler(TraderHandler):
    def invoke(self, task: dict, request: ModelRequest):
        # Trader retains its existing durable decision recovery and actual execution.
        result = self.gateway.invoke(
            request, deployment_id=self.deployment_id, price_card_id=self.price_card_id,
            fx_rate=self.gateway.router.config.fx_rate, fx_buffer=self.gateway.router.config.fx_buffer,
            invocation_id=f"trader:{task['task_id']}", portfolio_id=task["portfolio_id"],
            authorize=lambda: self._eligible(task),
        )
        row = self.database.execute("SELECT state FROM model_invocations WHERE invocation_id = ?",
                                    (f"trader:{task['task_id']}",)).fetchone()
        if row and row["state"] == "UNCERTAIN":
            raise ValidationFailure("Model outcome/billing requires reconciliation")
        return result


class RoutedHandler:
    """Creates an immutable concrete handler for the route pinned in each task."""

    def __init__(self, role: str, router: ModelRouter, build) -> None:
        self.role, self.router, self.build = role, router, build
        self.manages_attempts = role == "engineer"

    def context(self, task: dict) -> dict:
        try:
            route = self.router.resolve(task)
            context = self.build(route).context(task)
            return {**context, "model_route": route}
        except TradeGraphError as exc:
            return {"model_route_error": str(exc)[:500]}

    def recover(self, task: dict) -> dict | None:
        db = self.router.budget.database
        completed = db.execute("SELECT document_json FROM role_results WHERE task_id = ? AND portfolio_id = ?",
                               (task["task_id"], task["portfolio_id"])).fetchone()
        if completed:
            return json.loads(completed[0])
        # Recovery precedes active artifact loading and approval checks. It settles
        # an old effect even if the owner has since disabled/removed its route.
        invocation = db.execute("SELECT request_json, reservation_id FROM model_invocations WHERE task_id = ? "
                                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (task["task_id"],)).fetchone()
        if invocation is not None:
            request = ModelRequest.model_validate_json(invocation["request_json"])
            reservation = db.execute("SELECT price_card_id FROM budget_reservations WHERE reservation_id = ?",
                                     (invocation["reservation_id"],)).fetchone()
            route = {"provider": request.provider, "model": request.model, "price_card_id": reservation[0]}
            return self.build(route).recover(task)
        # Engineer can have persisted a job before creating the gateway invocation.
        if self.role == "engineer":
            job = db.execute("SELECT document_json FROM engineering_jobs WHERE task_id = ?",
                             (task["task_id"],)).fetchone()
            if job:
                return self.build(json.loads(job[0])["billing"]).recover(task)
        return None

    def __call__(self, task: dict) -> dict:
        route = task["snapshot"].get("model_route")
        if route is None:
            reason = task["snapshot"].get("model_route_error", "persisted model route is missing")
            return self._failed(task, reason)
        try:
            if route != self.router.resolve(task):
                raise AuthorityDenied("persisted model route no longer matches the verified artifact")
        except TradeGraphError as exc:
            return self._failed(task, str(exc))
        return self.build(route)(task)

    def _failed(self, task: dict, reason: str) -> dict:
        output = {"_status": "FAILED", "reason": reason[:500], "usage_reservations": []}
        db = self.router.budget.database
        with db.immediate():
            self.router.artifact_runtime.scheduler.leased_row(task["_lease"])
            db.execute("INSERT INTO role_results VALUES (?, ?, ?, 'FAILED', ?, ?)",
                       (task["task_id"], task["portfolio_id"], self.role,
                        json.dumps(output, sort_keys=True), self.router.artifact_runtime.scheduler.now()))
        return output


@dataclass(frozen=True)
class RuntimeHandlers:
    gateway: RuntimeGateway
    router: ModelRouter
    handlers: dict[str, RoutedHandler]


def assemble_handlers(office, secretary, engineer, artifact_runtime, config: RuntimeModelConfig, *,
                      workspace_root: Path, api_keys: dict[str, str] | None = None, transport=None) -> RuntimeHandlers:
    """Assemble all roles without funding, enabling calls or performing network I/O."""
    router = ModelRouter(office.budget, artifact_runtime, config, api_keys=api_keys)
    engineer.validate_model_routes = router.validate_bundle
    gateway = RuntimeGateway(
        office.budget, router=router, paid_calls_enabled=config.paid_calls_enabled,
        api_keys=api_keys,
        transport=transport or (HttpxProviderHttp() if config.paid_calls_enabled and api_keys else None),
    )

    def build(role: str, route: dict):
        common = {"deployment_id": config.deployment_id, "price_card_id": route["price_card_id"],
                  "provider": route["provider"], "model": route["model"], "artifact_runtime": artifact_runtime}
        if role == "engineer":
            return EngineerHandler(engineer, office.scheduler, gateway, **common, secretary=secretary,
                                   workspace_root=workspace_root, fx_rate=config.fx_rate, fx_buffer=config.fx_buffer)
        handler_type = {"leader": RuntimeLeaderHandler, "trader": RuntimeTraderHandler,
                        "research": RuntimeResearchHandler, "learning": RuntimeLearningHandler,
                        "optimisation": RuntimeOptimisationHandler}[role]
        handler = handler_type(office, secretary, gateway, **common)
        if role == "optimisation":
            handler.engineer = engineer
        return handler

    handlers = {role: RoutedHandler(role, router, lambda route, role=role: build(role, route)) for role in MODEL_ROLES}
    return RuntimeHandlers(gateway, router, handlers)
