"""Fixed-provider subscription departmental assembly with no API pricing or automatic repairs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from trade_graph.adapters.engineering.artifact_files import content_hash
from trade_graph.adapters.models.subscription import SubscriptionAdapter, SubscriptionConfig, SubscriptionJournal
from trade_graph.adapters.persistence.db import atomic
from trade_graph.application.authority import AuthorityRecord
from trade_graph.application.change_authority import authorized_change
from trade_graph.application.engineering_workflow import EngineerHandler
from trade_graph.application.leadership import GatewayRole, LeaderHandler
from trade_graph.application.runtime_departments import LearningHandler, OptimisationHandler, ResearchHandler
from trade_graph.application.runtime_models import MODEL_ROLES
from trade_graph.application.trader_workflow import TraderHandler
from trade_graph.contracts.models import ContractModel, ModelResult
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError, ValidationFailure
from trade_graph.kernel.department_gateway import public_context
from trade_graph.kernel.subscription_gateway import ProtectedSubscriptionGateway

# Immutable per-role keys. Mutable prompts/configuration cannot expand these.
_COMMON = frozenset({"objective", "evidence_refs", "source_instruction"})
ROLE_CONTEXT_KEYS = {
    "research": _COMMON | {"as_of", "market", "strategy_templates", "active_strategy_templates", "sources",
                            "analysis_instruction", "reports"},
    "trader": _COMMON | {"as_of", "artifact", "market", "portfolio", "selected_context", "strategy_templates",
                          "active_strategy_templates", "research"},
    "learning": _COMMON | {"reports", "decisions", "lesson_revisions", "analysis_instruction"},
    "optimisation": _COMMON | {"reports", "expenses", "task_counts", "analysis_instruction"},
    "leader": _COMMON | {"reports", "proposals", "candidates", "consultation_result"},
    "engineer": frozenset({"change", "source_files", "source_instruction", "previous_findings"}),
}


class SubscriptionRuntimeConfig(ContractModel):
    deployment_id: str = Field(default="deployment", min_length=1, max_length=128)
    subscription: SubscriptionConfig

    @model_validator(mode="after")
    def fixed_context_allowlist(self):
        configured = self.subscription.allowed_context_keys
        if configured and set(configured) != set(ROLE_CONTEXT_KEYS):
            raise ValueError("departmental subscription context allowlist requires all six roles")
        if any(role not in ROLE_CONTEXT_KEYS or not set(keys).issubset(ROLE_CONTEXT_KEYS[role])
               for role, keys in configured.items()):
            raise ValueError("subscription role context exceeds the protected allowlist")
        if not configured:
            self.subscription = self.subscription.model_copy(update={
                "allowed_context_keys": {role: sorted(keys) for role, keys in ROLE_CONTEXT_KEYS.items()}})
        return self


class SubscriptionRouter:
    roles = frozenset(MODEL_ROLES)

    def __init__(self, budget, artifact_runtime, config, adapter):
        self.budget, self.artifact_runtime = budget, artifact_runtime
        self.config, self.adapter = config.model_copy(deep=True), adapter
        if self.config.subscription != adapter.config:
            raise AuthorityDenied("subscription adapter differs from the protected fixed profile")

    def resolve(self, task, *, require_available=True):
        if task["role"] not in self.roles:
            raise AuthorityDenied("unknown subscription department")
        bundle = self.artifact_runtime.bundle_for(task)
        self.validate_bundle(bundle)
        return {"provider": "openai" if self.config.subscription.provider == "codex_subscription" else "anthropic",
                "model": self.config.subscription.model, "source": "protected-subscription-profile",
                "artifact": self.artifact_runtime.identity(bundle), "billing_kind": "subscription"}

    def validate_bundle(self, bundle):
        if self.artifact_runtime.model_routes(bundle):
            raise AuthorityDenied("mutable artifacts cannot change the fixed subscription provider/model")

    def readiness(self, portfolio_id=None):
        status = self.adapter.readiness.public_status()
        paused = SubscriptionJournal(self.budget.database, self.budget.clock).provider_status(
            self.config.subscription.provider)["ai_paused"]
        ready = status["ready"] and not paused and self.config.subscription.provider != "codex_subscription"
        return {**status, "ready": ready, "selected_provider": self.config.subscription.provider,
                "roles": {role: {"provider": self.config.subscription.provider,
                          "model": self.config.subscription.model, "ready": ready} for role in MODEL_ROLES}}


class SubscriptionGateway:
    """Budget views are context/authority only; dispatch never reserves or prices an API call."""

    def __init__(self, router):
        self.router, self.budget, self.adapter = router, router.budget, router.adapter
        self.journal = SubscriptionJournal(self.budget.database, self.budget.clock)

    def assert_request(self, request):
        expected = "openai" if self.router.config.subscription.provider == "codex_subscription" else "anthropic"
        if request.provider != expected or request.model != self.router.config.subscription.model or request.synthetic:
            raise AuthorityDenied("subscription request differs from its protected exact provider/model")

    def invoke(self, request, *, invocation_id, authorize, cancel_event=None, **kwargs):
        self.assert_request(request)
        authorize()
        allowed = self.router.config.subscription.allowed_context_keys.get(request.role, [])
        context = public_context({key: value for key, value in request.model_dump(mode="json")["context"].items()
                                  if key in allowed})
        selected = request.model_copy(update={"context": context})
        return self.adapter.invoke(selected, invocation_id=invocation_id, journal=self.journal,
                                   cancel_event=cancel_event)


class SubscriptionDepartmentMixin:
    def _envelope(self, task: dict) -> dict:
        envelope = ResearchHandler._envelope(self, task)
        portfolio = self.database.execute("SELECT mode FROM portfolios WHERE portfolio_id=?",
                                          (task["portfolio_id"],)).fetchone()
        if portfolio is None:
            raise AuthorityDenied("subscription journal requires a persisted portfolio")
        return {**envelope, "mode": portfolio["mode"]}

    def _eligible(self, task: dict, *, compare: bool = True) -> None:
        row = self.scheduler.leased_row(task["_lease"])
        if task["role"] not in self.allowed_roles:
            raise AuthorityDenied("handler is not authorized for this department")
        if row["role"] != task["role"] or row["portfolio_id"] != task["portfolio_id"]:
            raise AuthorityDenied("worker attribution mismatch")
        current = self.guard(task["portfolio_id"])
        if compare and current != task["snapshot"]["guard"]:
            raise StaleState("authority/version/pause/budget changed after snapshot")
        pause = current["pause"]
        if pause and pause["profile"] != "RUNNING" and pause["originator"] != "leader":
            raise AuthorityDenied("owner/system halt prevents new model work and Leader effects")
        if self.office.execution.authority.active_mandate(task["portfolio_id"]).expires_at_utc <= self.clock.now():
            raise AuthorityDenied("mandate expired")
        policy = self.office.execution.authority.active_policy()
        if not current["budget"] or row["allocated_spend"] is None:
            raise AuthorityDenied("persisted monetary allocation required")
        if Decimal(row["allocated_spend"]) > policy.root_paid_limit.amount:
            raise AuthorityDenied("task exceeds current owner root limit")
        config = current["budget"]
        if (
            Decimal(row["allocated_spend"]) > Decimal(config["root_limit"])
            or Decimal(config["total_allowance"]) > policy.monthly_operating.amount
            or Decimal(config["daily_limit"]) > policy.daily_paid_limit.amount
        ):
            raise AuthorityDenied("deployment budget exceeds owner envelope")
        if row["deadline_at"] and row["deadline_at"] <= self.scheduler.now():
            raise AuthorityDenied("task deadline expired")
        if row["attempts_used"] > min(row["max_attempts"], policy.ordinary_max_paid_attempts):
            raise AuthorityDenied("owner attempt limit exceeded")
        if current["active"] and task["system_version_id"] != current["active"]["artifact_hash"]:
            raise StaleState("worker artifact version is not active")
        if self.artifact_runtime:
            self.artifact_runtime.assert_task(task)


        if task["role"] == "trader" and pause and pause["profile"] != "RUNNING":
            raise AuthorityDenied("paused mutable decisions cannot dispatch Trader inference")

    def _invocation_id(self, task):
        return f"{task['role']}:{task['task_id']}"

    def invoke(self, task, request):
        return self.gateway.invoke(request, deployment_id=self.deployment_id, invocation_id=self._invocation_id(task),
                                   portfolio_id=task["portfolio_id"], authorize=lambda: self._eligible(task))

    def recover(self, task):
        completed = GatewayRole.recover(self, task)
        if completed is not None:
            return completed
        with self.database.immediate():
            self.scheduler.leased_row(task["_lease"])
            row = self.database.execute("SELECT * FROM subscription_invocations WHERE invocation_id=? AND task_id=?",
                                        (self._invocation_id(task), task["task_id"])).fetchone()
            if row is None:
                return None
            result = self.gateway._journal_result(row)
            snapshot = self.database.execute(
                "SELECT payload_json FROM snapshots WHERE snapshot_id=? AND portfolio_id=?",
                (row["run_id"], task["portfolio_id"])).fetchone()
            if snapshot is None:
                return self._record(task, {"_status": "FAILED", "reason": "durable subscription snapshot missing"})
            task.update(snapshot=json.loads(snapshot[0]), snapshot_id=row["run_id"],
                        system_version_id=row["system_version_id"])
            if result.failure == "timeout_uncertain":
                return self._record(task, {"_status": "WAITING_EXTERNAL",
                                           "reason": "Subscription outcome requires reconciliation; no replay"})
        try:
            if not result.ok:
                raise ValidationFailure(f"subscription {result.failure}: {result.message}")
            return self.apply(task, self.reply_type.model_validate(result.payload))
        except (TradeGraphError, ValidationError) as exc:
            with self.database.immediate():
                self.scheduler.leased_row(task["_lease"])
                return self._record(task, {"_status": "FAILED", "reason": str(exc)[:500]})

    def __call__(self, task):
        # Explicitly bypass API-specific Trader recovery/result handling.
        output = GatewayRole.__call__(self, task)
        row = self.database.execute("SELECT state FROM subscription_invocations WHERE invocation_id=?",
                                    (self._invocation_id(task),)).fetchone()
        if row and row["state"] == "UNCERTAIN":
            with self.database.immediate():
                self.scheduler.leased_row(task["_lease"])
                output["_status"] = "WAITING_EXTERNAL"
                self.database.execute("UPDATE role_results SET status=?,document_json=? WHERE task_id=?",
                                      ("WAITING_EXTERNAL", json.dumps(output, sort_keys=True), task["task_id"]))
        return output


class SubscriptionResearchHandler(SubscriptionDepartmentMixin, ResearchHandler):
    pass


class SubscriptionTraderHandler(SubscriptionDepartmentMixin, TraderHandler):
    @atomic
    def apply(self, task: dict, reply) -> dict:
        self._eligible(task)
        if not set(reply.evidence_ids).issubset(task["snapshot"]["evidence_refs"]):
            raise ValidationFailure("Trader cites research outside its persisted snapshot")
        turn = self.trader.act(
            task["portfolio_id"], ModelResult(ok=True, payload=reply.model_dump(mode="json")),
            snapshot_id=task["snapshot_id"], task_id=task["task_id"],
            root_task_id=task["root_task_id"], run_id=task["snapshot_id"],
            system_version_id=task["system_version_id"],
            template_documents=task["snapshot"]["strategy_templates"],
            snapshot_feature_refs=[item["history"]["source_ref"]
                                   for item in task["snapshot"].get("market", {}).values()
                                   if item.get("history", {}).get("event_time_utc")
                                   and item["history"]["available_at_utc"]
                                   and item["history"]["source_ref"] in task["snapshot"]["evidence_refs"]],
        )
        invocation = self.database.execute(
            "SELECT result_json FROM subscription_invocations WHERE invocation_id = ?",
            (f"trader:{task['task_id']}",),
        ).fetchone()
        result = ModelResult.model_validate_json(invocation["result_json"])
        allowed = self.gateway.router.config.subscription.allowed_context_keys["trader"]
        plan = self.gateway._row(f"trader:{task['task_id']}", "invoke_model")
        original = json.loads(plan["scope_json"])["original_request"]["context"]
        context = public_context({key: value for key, value in original.items() if key in allowed})
        usage = result.usage
        report_id = self.secretary.report(
            task["portfolio_id"], role="trader", kind="decision", summary=reply.rationale,
            evidence_refs=[turn.decision_id, task["snapshot_id"]], source_key=task["task_id"], material=False,
        )
        output = {
            "decision_id": turn.decision_id, "intent_id": turn.intent_id, "action": turn.action,
            "report_id": report_id,
            "artifact": task["snapshot"]["artifact"],
            "context_bytes": len(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()),
            "input_tokens": (usage.uncached_input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
                             if usage else None),
            "output_tokens": usage.billed_output_tokens if usage else None,
        }
        return self._record(task, output)




class SubscriptionLearningHandler(SubscriptionDepartmentMixin, LearningHandler):
    pass


class SubscriptionOptimisationHandler(SubscriptionDepartmentMixin, OptimisationHandler):
    pass


class SubscriptionLeaderHandler(SubscriptionDepartmentMixin, LeaderHandler):
    def _action(self, task, decision_id, action, evidence_refs):
        if action.kind == "activate":
            proof = self.database.execute("SELECT report_json FROM candidate_attestations WHERE candidate_id=?",
                                          (action.candidate_id,)).fetchone()
            if proof:
                self.gateway.router.validate_bundle({"files": json.loads(proof[0])["artifact_files"]})
        return super()._action(task, decision_id, action, evidence_refs)


class SubscriptionEngineerHandler(EngineerHandler):
    def _eligible(self, task: dict):
        worker = self.scheduler.leased_row(task["_lease"])
        if (worker["task_id"] != task["task_id"] or worker["role"] != "engineer"
                or task["role"] != "engineer" or worker["portfolio_id"] != task["portfolio_id"]
                or worker["root_task_id"] != task["root_task_id"]):
            raise AuthorityDenied("Engineer worker attribution mismatch")
        change, _, commission = authorized_change(
            self.database, self.clock, task["portfolio_id"], task["input"].get("change_id"),
        )
        if change.task_id != worker["task_id"] or task["system_version_id"] != change.baseline_hash:
            raise AuthorityDenied("commission worker/version mismatch")
        policy = AuthorityRecord(self.database, self.clock).active_policy()
        budget = self.database.execute("SELECT * FROM deployment_budget WHERE deployment_id = ?",
                                       (self.deployment_id,)).fetchone()
        if (worker["allocated_spend"] is None or Decimal(worker["allocated_spend"]) != change.max_spend.amount
                or budget is None or change.max_spend.amount > policy.root_paid_limit.amount
                or change.max_spend.amount > Decimal(budget["root_limit"])
                or Decimal(budget["total_allowance"]) > policy.monthly_operating.amount
                or Decimal(budget["daily_limit"]) > policy.daily_paid_limit.amount
                or change.max_steps > policy.engineer_max_paid_attempts
                or worker["max_attempts"] != change.max_steps):
            raise AuthorityDenied("commission exceeds persisted monetary/attempt envelope")
        if content_hash(self.engineer.source_files(task["portfolio_id"])) != change.baseline_hash:
            raise StaleState("source baseline moved before generation; revalidate")
        job = self._job(task)
        if job and job["commission_hash"] != commission["task_hash"]:
            raise StaleState("commission changed after job creation")
        if job and job["billing"] != self._billing():
            raise StaleState("Engineer provider/model/billing changed during an unfinished job")
        if job and content_hash(job["snapshot"].get("source_files", {})) != change.baseline_hash:
            raise StaleState("persisted Engineer source snapshot does not match commissioned baseline")
        if self.artifact_runtime and task.get("snapshot"):
            self.artifact_runtime.assert_task(task)
        return change, commission


    def _billing(self):
        return {"provider": self.provider, "model": self.model, "deployment_id": self.deployment_id,
                "billing_kind": "subscription"}

    def recover(self, task):
        with self.database.immediate():
            self.scheduler.leased_row(task["_lease"])
            job = self._job(task)
            if job and job["phase"] == "REQUESTING":
                row = self.database.execute(
                    "SELECT * FROM subscription_invocations WHERE invocation_id=? AND task_id=?",
                    (job["invocation_id"], task["task_id"])).fetchone()
                if row:
                    self.gateway._journal_result(row)
        result = self.database.execute("SELECT document_json FROM role_results WHERE task_id=? AND portfolio_id=? "
                                       "AND role='engineer'", (task["task_id"], task["portfolio_id"])).fetchone()
        if result:
            return json.loads(result[0])
        if job is None:
            return None
        task.update(snapshot=job["snapshot"], snapshot_id=job["run_id"])
        return job["output"] if job["phase"] == "TERMINAL" else self(task)

    def _retry(self, task, job, reason, kind, candidate_id=None):
        return self._terminal(task, job, "FAILED", reason, candidate_id)

    def _schema_failure(self, task, job, reason):
        return self._terminal(task, job, "FAILED", reason)

    def _terminal(self, task, job, status, reason, candidate_id=None):
        row = self.database.execute("SELECT state FROM subscription_invocations WHERE invocation_id=?",
                                    (job["invocation_id"],)).fetchone() if job else None
        if row and row["state"] == "UNCERTAIN":
            status, reason = "WAITING_EXTERNAL", "Subscription outcome requires reconciliation; no replay"
        return super()._terminal(task, job, status, reason, candidate_id)


class SubscriptionRoutedHandler:
    def __init__(self, role, router, build):
        self.role, self.router, self.build = role, router, build
        self.manages_attempts = role == "engineer"

    def context(self, task):
        try:
            route = self.router.resolve(task)
            return {**self.build(route).context(task), "model_route": route}
        except TradeGraphError as exc:
            return {"model_route_error": str(exc)[:500]}

    def recover(self, task):
        return self.build({"provider": "openai" if self.router.config.subscription.provider == "codex_subscription"
                           else "anthropic", "model": self.router.config.subscription.model}).recover(task)

    def __call__(self, task):
        try:
            route = self.router.resolve(task)
            if task["snapshot"].get("model_route") != route:
                raise AuthorityDenied("persisted subscription route no longer matches the protected profile")
            return self.build(route)(task)
        except TradeGraphError as exc:
            output = {"_status": "FAILED", "reason": str(exc)[:500], "usage_reservations": []}
            db = self.router.budget.database
            with db.immediate():
                self.router.artifact_runtime.scheduler.leased_row(task["_lease"])
                db.execute("INSERT INTO role_results VALUES (?, ?, ?, 'FAILED', ?, ?)",
                    (task["task_id"], task["portfolio_id"], self.role, json.dumps(output, sort_keys=True),
                     self.router.artifact_runtime.scheduler.now()))
            return output


@dataclass(frozen=True)
class SubscriptionHandlers:
    gateway: ProtectedSubscriptionGateway
    router: SubscriptionRouter
    handlers: dict[str, SubscriptionRoutedHandler]


def assemble_subscription_handlers(office, secretary, engineer, artifact_runtime, config: SubscriptionRuntimeConfig, *,
                                   protected_runtime, workspace_root: Path, adapter: SubscriptionAdapter):
    """Assemble all roles without inference, API credentials, pricing or live authority."""
    router = SubscriptionRouter(office.budget, artifact_runtime, config, adapter)
    engineer.validate_model_routes = router.validate_bundle
    gateway = ProtectedSubscriptionGateway(SubscriptionGateway(router), protected_runtime)
    gateway.secretary = secretary

    def build(role, route):
        common = {"deployment_id": config.deployment_id, "price_card_id": "subscription-no-api-card",
                  "provider": route["provider"], "model": route["model"], "artifact_runtime": artifact_runtime}
        if role == "engineer":
            handler = SubscriptionEngineerHandler(engineer, office.scheduler, gateway, **common,
                                                  secretary=secretary, workspace_root=workspace_root)
            eligible_original, terminal_original = handler._eligible, handler._terminal

            def eligible(task):
                result = eligible_original(task)
                job = handler._job(task)
                if job and job["phase"] == "REQUESTING":
                    gateway.assert_engineer_request(task)
                return result

            def terminal(task, job, status, reason, candidate_id=None):
                with handler.database.immediate():
                    if status == "SUCCEEDED":
                        gateway.assert_application(task)
                    output = terminal_original(task, job, status, reason, candidate_id)
                    if output.get("_status") == "SUCCEEDED":
                        gateway.record_effect(task)
                    return output

            handler._eligible, handler._terminal = eligible, terminal
        else:
            kind = {"research": SubscriptionResearchHandler, "trader": SubscriptionTraderHandler,
                    "learning": SubscriptionLearningHandler, "optimisation": SubscriptionOptimisationHandler,
                    "leader": SubscriptionLeaderHandler}[role]
            handler = kind(office, secretary, gateway, **common)
            if role == "optimisation":
                handler.engineer = engineer
            apply_original = handler.apply

            def apply(task, reply):
                transformed = gateway.recover_reply(task, reply, handler)
                with handler.database.immediate():
                    gateway.assert_application(task)
                    output = apply_original(task, transformed)
                    gateway.record_effect(task)
                    return output

            handler.apply = apply
        return handler

    handlers = {role: SubscriptionRoutedHandler(role, router, lambda route, role=role: build(role, route))
                for role in MODEL_ROLES}
    return SubscriptionHandlers(gateway, router, handlers)
