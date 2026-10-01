"""Authenticated, revisioned operator commands backed by protected authority."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from trade_graph.api.security import redact
from trade_graph.application.activation import VersionController
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.budget import OPEN_STATES, BudgetGateway
from trade_graph.application.scheduler import Scheduler
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, StaleState, ValidationFailure
from trade_graph.domain.money import Money, canonical_decimal, parse_decimal

Amount = Annotated[Decimal, BeforeValidator(parse_decimal), Field(ge=0)]
Fraction = Annotated[Decimal, BeforeValidator(parse_decimal), Field(ge=0, le=1)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
TaskRole = Literal["research", "learning", "optimisation", "trader", "secretary", "leader"]
_CHANGE_CLASSES = frozenset({"artifact_config", "context_policy", "prompt", "schedule", "report_template"})
_ACTIVE_TASKS = frozenset({"QUEUED", "LEASED", "RUNNING", "WAITING_EXTERNAL", "BLOCKED_BUDGET", "PROPOSED"})


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier | None = None
    expected_revision: Annotated[StrictInt, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def revision_pair(self):
        if (self.request_id is None) != (self.expected_revision is None):
            raise ValueError("request_id and expected_revision must be supplied together")
        return self


class BudgetCommand(Command):
    deployment_id: Identifier = "deployment"
    total: Amount
    period: Amount
    priority_reserve: Amount
    daily: Amount
    root: Amount
    roles: dict[TaskRole | Literal["engineer"], Amount] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def nested_limits(self):
        if self.priority_reserve > self.total or any(
            amount > self.total for amount in (self.period, self.daily, self.root, *self.roles.values())
        ):
            raise ValueError("budget components exceed total allowance")
        return self


class PauseCommand(Command):
    profile: Literal["PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN", "STOPPED"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)] = "owner"
    position_policy: Literal["manage-only", "flatten"] | None = None


class LiveCommand(Command):
    # Legacy clients supplied these diagnostic amounts; neither authorizes live mode.
    paper_capital: Amount | None = None
    live_allocation: Amount | None = None


class ConfigCommand(Command):
    request_id: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    allowed_venues: list[str] | None = Field(default=None, min_length=1, max_length=16)
    allowed_symbols: list[str] | None = Field(default=None, min_length=1, max_length=64)
    allowed_change_classes: list[str] | None = Field(default=None, min_length=1, max_length=5)
    maximum_gross_exposure_fraction: Fraction | None = None
    maximum_single_asset_exposure_fraction: Fraction | None = None
    maximum_quote_age_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] | None = None
    budget_exhaustion_profile: Literal["PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "FLATTEN"] | None = None

    @field_validator("allowed_venues", "allowed_symbols", "allowed_change_classes")
    @classmethod
    def unique_names(cls, value):
        if value is not None and (
            len(set(value)) != len(value) or any(not item or len(item) > 128 or item.strip() != item for item in value)
        ):
            raise ValueError("configuration names must be unique and bounded")
        return value

    @model_validator(mode="after")
    def update_required(self):
        updates = self.model_dump(exclude={"request_id", "expected_revision"}, exclude_unset=True)
        if not updates or any(value is None for value in updates.values()):
            raise ValueError("at least one concrete configuration update is required")
        return self


class TaskCommand(Command):
    request_id: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    role: TaskRole
    objective: Annotated[str, Field(min_length=1, max_length=2000)]
    allocated_spend: Amount
    parent_id: Identifier | None = None
    root_task_id: Identifier | None = None
    max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 1

    @field_validator("objective")
    @classmethod
    def nonblank_objective(cls, value):
        if not value.strip():
            raise ValueError("objective is required")
        return value


class ActivateCommand(Command):
    candidate_id: Identifier
    baseline_hash: Identifier
    content_hash: Identifier
    # Old clients sent a label; trusted controller provenance remains authoritative.
    attestation: dict[Identifier, Annotated[str, Field(max_length=256)]] | None = Field(
        default=None,
        max_length=16,
        exclude=True,
    )


async def _body(request: Request, model: type[Command], *, empty: bool = False) -> Command:
    try:
        raw = await request.body()
        if len(raw) > 65536:
            raise ValueError("control request is too large")
        value = json.loads(raw) if raw else ({} if empty else None)
        return model.model_validate(value)
    except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
        # Validation errors can include the supplied secrets and arbitrary paths.
        raise HTTPException(status_code=422, detail="invalid control request") from exc


def _deployment(runtime) -> str:
    return getattr(runtime, "deployment_id", "deployment")


def _scope(runtime, role: str) -> str:
    # Policy and expense allowances are shared by every portfolio in a deployment.
    return f"owner:{_deployment(runtime)}" if role == "owner" else f"leader:{runtime.portfolio_id}"


def _revision(runtime, scope: str) -> int:
    row = runtime.database.execute("SELECT revision FROM dashboard_control_state WHERE scope = ?", (scope,)).fetchone()
    return 0 if row is None else row["revision"]


def configuration(runtime) -> dict:
    """The owner page and API share this single read snapshot."""
    with runtime.database.snapshot():
        authority = AuthorityRecord(runtime.database, runtime.clock)
        try:
            policy = authority.active_policy().model_dump(mode="json")
        except AuthorityDenied:
            policy = None
        deployment = _deployment(runtime)
        row = runtime.database.execute(
            "SELECT * FROM deployment_budget WHERE deployment_id = ?",
            (deployment,),
        ).fetchone()
        budget = None
        if row:
            roles = runtime.database.execute(
                "SELECT role, amount FROM role_allocations WHERE deployment_id = ? ORDER BY role",
                (deployment,),
            ).fetchall()
            budget = {
                "deployment_id": deployment,
                "currency": row["currency"],
                "total": row["total_allowance"],
                "period": row["period_allowance"],
                "priority_reserve": row["priority_reserve"],
                "daily": row["daily_limit"],
                "root": row["root_limit"],
                "roles": {item["role"]: item["amount"] for item in roles},
            }
        scope = _scope(runtime, "owner")
        pending = runtime.database.execute(
            "SELECT command_id, status, created_at FROM dashboard_commands "
            "WHERE scope = ? AND status = 'PROCESSING' ORDER BY created_at LIMIT 100",
            (scope,),
        ).fetchall()
        return redact(
            {
                "scope": scope,
                "revision": _revision(runtime, scope),
                "policy": policy,
                "budget": budget,
                "pause": runtime.execution.pause(runtime.portfolio_id),
                "pending_commands": [dict(row) for row in pending],
                "routing": {"supported": False, "reason": "persisted model routing is not configured in this runtime"},
            }
        )


class _Commands:
    def __init__(self, runtime):
        self.runtime = runtime
        self.database = runtime.database

    def _check(
        self,
        scope: str,
        body: Command,
        action: str,
        *,
        allow_processing: bool = False,
    ) -> tuple[str, str, int, dict | None]:
        document = json.dumps(
            {
                "action": action,
                "body": body.model_dump(mode="json"),
                "portfolio_id": self.runtime.portfolio_id,
                "deployment_id": _deployment(self.runtime),
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(document.encode()).hexdigest()
        command_id = body.request_id or str(uuid.uuid4())
        stored = self.database.execute(
            "SELECT * FROM dashboard_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if stored:
            if stored["scope"] != scope or stored["request_hash"] != digest:
                raise HTTPException(status_code=409, detail="request_id was used for a different command")
            if stored["status"] == "PROCESSING":
                raise HTTPException(status_code=409, detail="command is in progress; reconcile before retrying")
            result = json.loads(stored["response_json"])
            if stored["status"].startswith("FAILED:"):
                raise HTTPException(status_code=int(stored["status"].split(":")[1]), detail=result["detail"])
            return command_id, digest, result["revision"], result
        if (
            not allow_processing
            and self.database.execute(
                "SELECT 1 FROM dashboard_commands WHERE scope = ? AND status = 'PROCESSING'", (scope,)
            ).fetchone()
        ):
            raise HTTPException(status_code=409, detail="another command in this scope is in progress")
        revision = _revision(self.runtime, scope)
        if body.expected_revision is not None and body.expected_revision != revision:
            raise HTTPException(status_code=409, detail={"reason": "stale revision", "revision": revision})
        return command_id, digest, revision + 1, None

    def _save(self, command_id: str, scope: str, digest: str, revision: int, result: dict | None) -> None:
        self.database.execute(
            """INSERT INTO dashboard_control_state VALUES (?, ?)
            ON CONFLICT(scope) DO UPDATE SET revision = excluded.revision""",
            (scope, revision),
        )
        self.database.execute(
            "INSERT INTO dashboard_commands VALUES (?, ?, ?, ?, ?, ?)",
            (
                command_id,
                scope,
                digest,
                None if result is None else json.dumps(redact(result)),
                "PROCESSING" if result is None else "COMPLETE",
                utc_iso(self.runtime.clock.now()),
            ),
        )

    def mutate(
        self,
        scope: str,
        body: Command,
        action: str,
        effect: Callable[[], dict],
        *,
        allow_processing: bool = False,
    ) -> dict:
        with self.database.immediate():
            command_id, digest, revision, replay = self._check(scope, body, action, allow_processing=allow_processing)
            if replay is not None:
                return replay
            result = redact({**effect(), "revision": revision})
            self._save(command_id, scope, digest, revision, result)
            return result

    def begin(self, scope: str, body: Command, action: str, effect: Callable[[], None]) -> tuple[str, int, dict | None]:
        with self.database.immediate():
            command_id, digest, revision, replay = self._check(scope, body, action)
            if replay is None:
                effect()
                self._save(command_id, scope, digest, revision, None)
            return command_id, revision, replay

    def finish(self, command_id: str, revision: int, result: dict, *, error: int | None = None) -> dict:
        if error is not None:
            detail = result["detail"]
            detail = dict(detail) if isinstance(detail, dict) else {"reason": detail}
            result = {**result, "detail": {**detail, "command_id": command_id, "command_state": "FAILED"}}
        result = redact({**result, "revision": revision})
        with self.database.immediate():
            self.database.execute(
                "UPDATE dashboard_commands SET response_json = ?, status = ? "
                "WHERE command_id = ? AND status = 'PROCESSING'",
                (json.dumps(result), "COMPLETE" if error is None else f"FAILED:{error}", command_id),
            )
        if error is not None:
            raise HTTPException(status_code=error, detail=result["detail"])
        return result


def _domain(call):
    try:
        return call()
    except AuthorityDenied as exc:
        raise HTTPException(status_code=403, detail=redact(str(exc))) from exc
    except (StaleState, DuplicateRecord) as exc:
        raise HTTPException(status_code=409, detail=redact(str(exc))) from exc
    except (ValidationFailure, ValueError) as exc:
        raise HTTPException(status_code=422, detail=redact(str(exc))) from exc


def _policy_revision() -> str:
    return "owner-" + uuid.uuid4().hex


def _update_config(runtime, body: ConfigCommand) -> dict:
    authority = AuthorityRecord(runtime.database, runtime.clock)
    policy = authority.active_policy()
    updates = body.model_dump(exclude={"request_id", "expected_revision"}, exclude_unset=True)
    if "allowed_change_classes" in updates and not set(updates["allowed_change_classes"]).issubset(_CHANGE_CLASSES):
        raise AuthorityDenied("only registered non-executable change classes are supported")
    rows = runtime.database.execute("SELECT venue, symbol FROM instruments").fetchall()
    if "allowed_venues" in updates:
        supported = set(policy.allowed_venues) | {row["venue"] for row in rows}
        if not set(updates["allowed_venues"]).issubset(supported):
            raise AuthorityDenied("venue is not registered with the protected runtime")
    if "allowed_symbols" in updates:
        supported = set(policy.allowed_symbols) | {row["symbol"] for row in rows}
        if not set(updates["allowed_symbols"]).issubset(supported):
            raise AuthorityDenied("instrument is not registered with the protected runtime")
    updated = OwnerPolicy.model_validate({**policy.model_dump(), **updates, "revision_id": _policy_revision()})
    if updated.maximum_single_asset_exposure_fraction > updated.maximum_gross_exposure_fraction:
        raise ValidationFailure("single-asset exposure cannot exceed gross exposure")
    authority.install_policy(updated, role="owner")
    return {"policy": updated.model_dump(mode="json"), "routing": {"supported": False}}


def _budget(runtime, body: BudgetCommand) -> dict:
    if body.deployment_id != _deployment(runtime):
        raise AuthorityDenied("budget deployment does not match this runtime")
    gateway = BudgetGateway(runtime.database, runtime.clock)
    gateway.configure(
        deployment_id=body.deployment_id,
        currency="EUR",
        total=body.total,
        period=body.period,
        priority_reserve=body.priority_reserve,
        daily=body.daily,
        root=body.root,
        roles=body.roles,
    )
    # Owner budget writes revise both stores actually consumed by worker admission.
    authority = AuthorityRecord(runtime.database, runtime.clock)
    try:
        policy = authority.active_policy()
    except AuthorityDenied:
        policy = None
    if policy is not None:
        updated = OwnerPolicy.model_validate(
            {
                **policy.model_dump(),
                "revision_id": _policy_revision(),
                "monthly_operating": Money(amount=body.total, currency="EUR"),
                "priority_reserve": Money(amount=body.priority_reserve, currency="EUR"),
                "daily_paid_limit": Money(amount=body.daily, currency="EUR"),
                "root_paid_limit": Money(amount=body.root, currency="EUR"),
            }
        )
        authority.install_policy(updated, role="owner")
    return {
        "deployment_id": body.deployment_id,
        "currency": "EUR",
        "total": str(gateway.allowance(body.deployment_id)),
        "remaining": str(gateway.remaining(body.deployment_id)),
        "simulated_equity_separate": True,
    }


def _assign(runtime, body: TaskCommand) -> dict:
    authority = AuthorityRecord(runtime.database, runtime.clock)
    policy, mandate = authority.active_policy(), authority.active_mandate(runtime.portfolio_id)
    if mandate.expires_at_utc <= runtime.clock.now():
        raise AuthorityDenied("mandate expired")
    if runtime.execution.profile(runtime.portfolio_id) != "RUNNING":
        raise AuthorityDenied("pause blocks new assignments")
    if body.max_attempts > policy.ordinary_max_paid_attempts:
        raise AuthorityDenied("task attempts exceed owner bounds")
    if any(
        limit.currency != "EUR"
        for limit in (
            policy.root_paid_limit,
            policy.monthly_operating,
            policy.daily_paid_limit,
        )
    ):
        raise AuthorityDenied("task financial authority must be denominated in EUR")
    if body.allocated_spend > policy.root_paid_limit.amount:
        raise AuthorityDenied("task allocation exceeds owner root limit")
    deployment = _deployment(runtime)
    config = runtime.database.execute(
        "SELECT * FROM deployment_budget WHERE deployment_id = ?",
        (deployment,),
    ).fetchone()
    role = runtime.database.execute(
        "SELECT amount FROM role_allocations WHERE deployment_id = ? AND role = ?",
        (deployment, body.role),
    ).fetchone()
    if config is None or role is None:
        raise AuthorityDenied("persisted deployment and role allocation required")
    if (
        body.allocated_spend > Decimal(config["root_limit"])
        or Decimal(config["total_allowance"]) > policy.monthly_operating.amount
        or Decimal(config["daily_limit"]) > policy.daily_paid_limit.amount
    ):
        raise AuthorityDenied("task exceeds current deployment or owner envelope")
    scheduler = Scheduler(runtime.database, runtime.clock)
    scheduler.max_depth = min(scheduler.max_depth, policy.maximum_delegation_depth)
    scheduler.max_descendants = min(scheduler.max_descendants, policy.maximum_descendants_per_root)
    if body.parent_id:
        parent = runtime.database.execute("SELECT * FROM tasks WHERE task_id = ?", (body.parent_id,)).fetchone()
        if parent is None or parent["portfolio_id"] != runtime.portfolio_id or parent["status"] not in _ACTIVE_TASKS:
            raise AuthorityDenied("eligible parent in this portfolio is required")
    elif body.root_task_id:
        raise ValidationFailure("a root_task_id requires a parent_id")
    holds = runtime.database.execute(
        """SELECT task_id, root_task_id, role, amount FROM budget_reservations
        WHERE deployment_id = ? AND synthetic = 0 AND state IN ({})""".format(",".join("?" for _ in OPEN_STATES)),
        (deployment, *OPEN_STATES),
    ).fetchall()
    tasks = runtime.database.execute(
        "SELECT task_id, root_task_id, parent_id, role, allocated_spend, status FROM tasks"
    ).fetchall()
    role_used = sum((Decimal(h["amount"]) for h in holds if h["role"] == body.role), Decimal("0"))
    # Shared root ceilings bound overlapping parent/child role commitments.
    # Include the prospective child before applying that ceiling; its allocation
    # can consume an existing root envelope without creating another allowance.
    by_root = {}
    for task in tasks:
        by_root.setdefault(task["root_task_id"], []).append(dict(task))
    proposed_root = parent["root_task_id"] if body.parent_id else "prospective-root"
    by_root.setdefault(proposed_root, []).append(
        {
            "task_id": "prospective-task",
            "root_task_id": proposed_root,
            "parent_id": body.parent_id,
            "role": body.role,
            "allocated_spend": str(body.allocated_spend),
            "status": "QUEUED",
        }
    )
    role_committed = root_committed = Decimal("0")
    for root_id, members in by_root.items():
        root = next((t for t in members if t["parent_id"] is None), None)
        bound = min(policy.root_paid_limit.amount, Decimal(config["root_limit"]))
        if root and root["allocated_spend"] is not None:
            bound = min(bound, Decimal(root["allocated_spend"]))
        spent = sum((Decimal(h["amount"]) for h in holds if h["root_task_id"] == root_id), Decimal("0"))
        root_room = max(bound - spent, Decimal("0"))
        role_room = descendant_room = Decimal("0")
        for task in members:
            if task["status"] not in _ACTIVE_TASKS or task["allocated_spend"] is None:
                continue
            used = sum((Decimal(h["amount"]) for h in holds if h["task_id"] == task["task_id"]), Decimal("0"))
            remainder = max(Decimal(task["allocated_spend"]) - used, Decimal("0"))
            descendant_room += remainder
            if task["role"] == body.role:
                role_room += remainder
        role_committed += min(root_room, role_room)
        root_committed += min(root_room, descendant_room)
    if role_used + role_committed > Decimal(role["amount"]):
        raise AuthorityDenied("role monetary commitments exceed approved allocation")
    if root_committed > BudgetGateway(runtime.database, runtime.clock).remaining(deployment):
        raise AuthorityDenied("root monetary commitments exceed remaining deployment allowance")
    active = runtime.database.execute(
        "SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?",
        (runtime.portfolio_id,),
    ).fetchone()
    task_id = scheduler.add_task(
        role=body.role,
        objective=body.objective,
        portfolio_id=runtime.portfolio_id,
        parent_id=body.parent_id,
        root_task_id=body.root_task_id,
        allocated_spend=body.allocated_spend,
        max_attempts=body.max_attempts,
        expected_version=None if active is None else active["artifact_hash"],
        payload={"source": "authenticated Leader assignment", "policy_revision": policy.revision_id},
    )
    row = runtime.database.execute("SELECT root_task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return {
        "task_id": task_id,
        "root_task_id": row["root_task_id"],
        "role": body.role,
        "allocated_spend": canonical_decimal(body.allocated_spend),
        "status": "QUEUED",
    }


def _nonflat(runtime) -> bool:
    books = runtime.ledger.books(runtime.portfolio_id)
    if any(lot.open_quantity() != 0 for lot in books.lots):
        return True
    # Native deposits are holdings too, even if no trade lot/instrument exists.
    cash_assets = {"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK"}
    return any(amount != 0 and asset not in cash_assets for asset, amount in books.cash.items())


def _pause_profile(runtime, body: PauseCommand) -> str:
    outstanding = runtime.execution._has_outstanding(runtime.portfolio_id)
    if body.profile == "STOPPED" and (_nonflat(runtime) or outstanding):
        if body.position_policy is None:
            raise HTTPException(status_code=422, detail="nonflat stop requires position_policy manage-only or flatten")
        return "MANAGE_ONLY" if body.position_policy == "manage-only" else "FLATTEN"
    return body.profile


def _resume_barriers(runtime) -> list[str]:
    barriers = []
    if runtime.database.execute(
        """SELECT 1 FROM order_intents WHERE portfolio_id = ?
        AND state IN ('UNKNOWN', 'SUBMITTING', 'CANCEL_PENDING') LIMIT 1""",
        (runtime.portfolio_id,),
    ).fetchone():
        barriers.append("unresolved orders")
    if runtime.database.execute(
        "SELECT 1 FROM budget_reservations WHERE synthetic = 0 AND state = 'UNCERTAIN' LIMIT 1"
    ).fetchone():
        barriers.append("unresolved billing")
    if any(
        Decimal(row["unexplained"]) != 0
        for row in runtime.database.execute(
            "SELECT unexplained FROM invoice_reconciliations WHERE deployment_id = ?",
            (_deployment(runtime),),
        ).fetchall()
    ):
        barriers.append("unresolved invoice differences")
    rollout = runtime.database.execute(
        "SELECT state FROM version_rollouts WHERE portfolio_id = ? ORDER BY generation DESC LIMIT 1",
        (runtime.portfolio_id,),
    ).fetchone()
    if rollout and rollout["state"] in {"RESTART_PENDING", "RESTORE_PENDING", "ROLLBACK_PENDING", "BLOCKED"}:
        barriers.append("version recovery pending")
    pause = runtime.execution.pause(runtime.portfolio_id)
    if pause and pause["profile"] != "RUNNING":
        if pause["originator"] == "system":
            barriers.append("system pause requires controller recovery")
        if pause["profile"] in {"FLATTEN", "CANCEL_ALL", "STOPPED"} and pause["achieved"] not in {
            "flat-verified",
            "orders-cleared",
            "stopped",
        }:
            barriers.append("requested pause management is incomplete")
    return barriers


def register_controls(app: FastAPI, runtime, identity, owner_write) -> None:
    commands = _Commands(runtime)
    owner_scope, leader_scope = _scope(runtime, "owner"), _scope(runtime, "leader")

    def leader(request):
        if identity(request) != "leader":
            raise HTTPException(status_code=403, detail="leader role required")

    @app.get("/api/v1/owner/config")
    def owner_config(request: Request) -> dict:
        owner_write(request)
        return configuration(runtime)

    @app.post("/api/v1/owner/config")
    async def update_config(request: Request) -> dict:
        owner_write(request)
        body = await _body(request, ConfigCommand)
        return _domain(lambda: commands.mutate(owner_scope, body, "config", lambda: _update_config(runtime, body)))

    @app.post("/api/v1/owner/budgets")
    async def budgets(request: Request) -> dict:
        owner_write(request)
        body = await _body(request, BudgetCommand)
        return _domain(lambda: commands.mutate(owner_scope, body, "budgets", lambda: _budget(runtime, body)))

    @app.post("/api/v1/owner/enable-live")
    async def enable_live(request: Request) -> dict:
        owner_write(request)
        body = await _body(request, LiveCommand, empty=True)
        return commands.mutate(
            owner_scope,
            body,
            "enable-live",
            lambda: {
                "enabled": False,
                "reason": "live activation is not implemented; this API is paper-only",
            },
        )

    @app.post("/api/v1/owner/pause")
    async def pause(request: Request) -> dict:
        owner_write(request)
        body = await _body(request, PauseCommand)

        def persist():
            current = runtime.execution.pause(runtime.portfolio_id)
            if current and current["originator"] == "system" and current["profile"] != "RUNNING":
                raise HTTPException(status_code=409, detail="system pause requires controller recovery")
            runtime.execution.set_pause(runtime.portfolio_id, _pause_profile(runtime, body), "owner", body.reason)

        if body.profile == "MANAGE_ONLY":
            # This emergency latch is entirely local and remains available when
            # a prior external command was interrupted. Its revision fences any
            # delayed resume; the unknown prior command remains visible.
            def emergency():
                persist()
                runtime.execution._set_achieved(runtime.portfolio_id, "reconciliation-required")
                return {
                    "requested_profile": "MANAGE_ONLY",
                    "profile": "MANAGE_ONLY",
                    "originator": "owner",
                    "achieved": "reconciliation-required",
                    "management_continues": True,
                    "reconciliation_required": True,
                    "offline_protection": "only previously verified venue-native protection survives process shutdown",
                }

            return _domain(
                lambda: commands.mutate(
                    owner_scope,
                    body,
                    "pause",
                    emergency,
                    allow_processing=True,
                )
            )
        command_id, revision, replay = _domain(lambda: commands.begin(owner_scope, body, "pause", persist))
        if replay is not None:
            return replay
        try:
            achieved = await runtime.execution.advance_pause(runtime.portfolio_id)
            if achieved in {"flat-verified", "stopped"} and _nonflat(runtime):
                achieved = "unresolved-native-holdings"
                runtime.execution._set_achieved(runtime.portfolio_id, achieved)
        except Exception:
            return commands.finish(
                command_id, revision, {"detail": "pause persisted; management requires reconciliation"}, error=409
            )
        profile = runtime.execution.profile(runtime.portfolio_id)
        return commands.finish(
            command_id,
            revision,
            {
                "requested_profile": body.profile,
                "profile": profile,
                "originator": "owner",
                "achieved": achieved,
                "management_continues": profile != "STOPPED",
                "position_policy": body.position_policy,
                "offline_protection": "only previously verified venue-native protection survives process shutdown",
            },
        )

    @app.post("/api/v1/owner/resume")
    async def resume(request: Request) -> dict:
        owner_write(request)
        body = await _body(request, Command, empty=True)

        def preflight():
            if runtime.execution.profile(runtime.portfolio_id) == "RUNNING":
                runtime.execution.set_pause(
                    runtime.portfolio_id, "MANAGE_ONLY", "owner", "resume preflight reconciliation"
                )
                runtime.execution._set_achieved(runtime.portfolio_id, "reconciliation-required")

        command_id, revision, replay = commands.begin(owner_scope, body, "resume", preflight)
        if replay is not None:
            return replay
        try:
            await runtime.execution.reconcile()
        except Exception:
            return commands.finish(
                command_id, revision, {"detail": "reconciliation incomplete; pause retained"}, error=409
            )
        with runtime.database.immediate():
            barriers = _resume_barriers(runtime)
            if _revision(runtime, owner_scope) != revision:
                barriers.append("owner state changed during reconciliation; newer pause retained")
            if not barriers:
                runtime.execution.set_pause(runtime.portfolio_id, "RUNNING", "owner", "resume after reconcile")
            result = (
                {"detail": {"reason": "resume blocked", "barriers": barriers}}
                if barriers
                else {
                    "profile": "RUNNING",
                    "reconciled": True,
                }
            )
            if not barriers:
                # Commit resume together with its completed receipt, after external reconciliation.
                return commands.finish(command_id, revision, result)
        return commands.finish(command_id, revision, result, error=409)

    @app.post("/api/v1/leader/tasks")
    async def assign(request: Request) -> dict:
        leader(request)
        body = await _body(request, TaskCommand)
        return _domain(lambda: commands.mutate(leader_scope, body, "tasks", lambda: _assign(runtime, body)))

    @app.post("/api/v1/leader/activate")
    async def activate(request: Request) -> dict:
        leader(request)
        body = await _body(request, ActivateCommand)

        def effect():
            versions = VersionController(runtime.database, runtime.clock)
            versions.activate(runtime.portfolio_id, body.model_dump(exclude={"request_id", "expected_revision"}))
            return {"artifact_hash": versions.current_hash(runtime.portfolio_id)}

        return _domain(lambda: commands.mutate(leader_scope, body, "activate", effect))
