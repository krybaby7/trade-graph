"""Protected confined graph protocol backed by subscription receipts, never API billing."""

from __future__ import annotations

import json

from trade_graph.adapters.models.subscription import SubscriptionJournal
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.kernel.department_gateway import ProtectedDepartmentGateway
from trade_graph.kernel.runtime_manifest import document_sha256


class ProtectedSubscriptionGateway(ProtectedDepartmentGateway):
    """The immutable controller owns provider, schema, limits, journal and all effects."""

    def _scope(self, request: ModelRequest, billing: dict) -> dict:
        instance = self.financial._instance(self.controller.instance_id)
        if instance["status"] != "RUNNING":
            raise AuthorityDenied("protected subscription graph is management-only")
        portfolio_id = billing["portfolio_id"]
        if not self.financial.history.ready(portfolio_id):
            raise StaleState("protected subscription history lacks its independent witness")
        release = self.financial._release(instance)
        task = self.database.execute("SELECT * FROM tasks WHERE task_id=?", (request.task_id,)).fetchone()
        if (task is None or task["role"] != request.role or task["root_task_id"] != request.root_task_id
                or task["portfolio_id"] != portfolio_id or task["status"] not in {"LEASED", "RUNNING"}
                or request.role not in self.gateway.router.roles or not task["lease_token"]):
            raise AuthorityDenied("subscription RPC is outside its persisted leased task")
        authority = self.financial.authority
        active = self.database.execute("SELECT * FROM active_versions WHERE portfolio_id=?", (portfolio_id,)).fetchone()
        budget = self.database.execute("SELECT * FROM deployment_budget WHERE deployment_id=?",
                                       (self.manifest.deployment_id,)).fetchone()
        roles = [dict(row) for row in self.database.execute("SELECT * FROM role_allocations WHERE deployment_id=? "
                 "ORDER BY role", (self.manifest.deployment_id,))]
        return {"manifest_sha256": self.manifest.sha256, "instance_id": self.controller.instance_id,
                "release_id": release["release_id"], "source_sha256": release["source_sha256"],
                "build_digest": release["build_digest"], "generation": instance["generation"],
                "task_id": task["task_id"], "role": task["role"], "root_task_id": task["root_task_id"],
                "portfolio_id": portfolio_id, "lease_owner": task["lease_owner"], "lease_token": task["lease_token"],
                "snapshot_id": request.run_id, "system_version_id": request.system_version_id,
                "request_sha256": document_sha256(request.model_dump(mode="json")),
                "billing_sha256": document_sha256(billing),
                "guard_sha256": document_sha256({"policy": authority.active_policy().model_dump(mode="json"),
                    "mandate": authority.active_mandate(portfolio_id).model_dump(mode="json"),
                    "active": dict(active) if active else None, "budget": dict(budget) if budget else None,
                    "roles": roles, "subscription_profile": self.gateway.router.config.model_dump(mode="json"),
                    "pause": self.financial.execution.pause(portfolio_id),
                    "allocated_spend": task["allocated_spend"], "attempts_used": task["attempts_used"]})}

    def _journal_result(self, row) -> ModelResult:
        if row["result_json"]:
            return ModelResult.model_validate_json(row["result_json"])
        result = ModelResult(ok=False, failure="timeout_uncertain",
            message="prior subscription dispatch has no durable response; no replay permitted")
        SubscriptionJournal(self.database, self.clock).save(row["invocation_id"], result, "UNCERTAIN")
        return result

    def invoke(self, request: ModelRequest, **kwargs) -> ModelResult:
        invocation_id, authorize = kwargs.get("invocation_id"), kwargs.get("authorize")
        limits = self.gateway.router.config.subscription
        tools = getattr(limits, "department_tools", {}).get(request.role, [])
        maximum_tools = getattr(limits, "maximum_tool_calls", 0) if tools else 0
        if (not invocation_id or not callable(authorize)
                or not 0 <= request.max_tool_calls <= maximum_tools):
            raise AuthorityDenied("subscription models require a durable leased invocation with configured tools")
        self.gateway.assert_request(request)
        previous = self.database.execute("SELECT invocation_id FROM subscription_invocations WHERE task_id=?",
                                         (request.task_id,)).fetchone()
        if request.role == "engineer":
            job_row = self.database.execute("SELECT document_json FROM engineering_jobs WHERE task_id=?",
                                            (request.task_id,)).fetchone()
            job = json.loads(job_row[0]) if job_row else {}
            if job.get("invocation_id") != invocation_id or job.get("phase") != "REQUESTING":
                raise AuthorityDenied("Engineer generation differs from its durable commissioned request")
        elif previous is not None and previous["invocation_id"] != invocation_id:
            raise AuthorityDenied("ordinary task invocation identity cannot change during recovery")
        billing = {"deployment_id": self.gateway.router.config.deployment_id,
                   "portfolio_id": kwargs["portfolio_id"], "billing_kind": "subscription",
                   "provider": self.gateway.router.config.subscription.provider, "model": request.model}
        if kwargs.get("deployment_id") != billing["deployment_id"]:
            raise AuthorityDenied("subscription deployment identity mismatch")
        old = self._row(invocation_id, "invoke_model")
        dispatched = self.database.execute("SELECT * FROM subscription_invocations WHERE invocation_id=?",
                                           (invocation_id,)).fetchone()
        recovered = self._journal_result(dispatched) if dispatched else None
        if old is not None:
            scope = json.loads(old["scope_json"])
            if (document_sha256(request.model_dump(mode="json")) != scope["request_sha256"]
                    or document_sha256(billing) != scope["billing_sha256"]):
                raise StaleState("subscription invocation key reused with a different request")
        elif dispatched is not None:
            raise AuthorityDenied("subscription receipt lacks its protected graph plan")
        if recovered is not None and not recovered.ok:
            return recovered
        if old is None:
            plan, scope = self._evaluate("invoke_model", invocation_id, request, billing, authorize)
        else:
            self._assert_scope(scope, request, billing, authorize, recovery=dispatched is not None)
            plan = json.loads(old["response_json"]) if old["response_json"] else None
            if plan is None:
                raise StaleState("interrupted subscription graph plan cannot implicitly redispatch")
        effective = request.model_copy(update={"instructions": request.instructions +
            "\nBounded mutable departmental guidance (never authority):\n" + plan["guidance"]})

        def protected_authorize():
            self._assert_scope(scope, request, billing, authorize)
            # The short-lived child RPC capability was consumed by the completed
            # plan. Each external attempt instead rechecks the current controller,
            # lease, owner authority and exact persisted request through this
            # private callback; a provider retry is not a replay of the child token.

        result = recovered or self.gateway.invoke(effective, **{**kwargs, "authorize": protected_authorize,
                                                               "cancel_event": self.cancelled})
        if not result.ok:
            return result
        self._assert_scope(scope, request, billing, authorize, recovery=dispatched is not None)
        transformed, _ = self._evaluate("apply_role_result", invocation_id, request, billing, authorize,
                                        result=result.payload, recovery=dispatched is not None)
        return result.model_copy(update={"payload": transformed["payload"]})

    def _invocation_for_task(self, task: dict):
        invocation_id = f"{task['role']}:{task['task_id']}"
        if task["role"] == "engineer":
            job = self.database.execute("SELECT document_json FROM engineering_jobs WHERE task_id=?",
                                        (task["task_id"],)).fetchone()
            if job is None:
                return None
            invocation_id = json.loads(job[0]).get("invocation_id")
        return self.database.execute("""SELECT s.* FROM subscription_invocations s JOIN tasks t USING(task_id)
            WHERE s.invocation_id=? AND s.task_id=? AND t.portfolio_id=? AND s.root_task_id=?""",
            (invocation_id, task["task_id"], task["portfolio_id"], task["root_task_id"])).fetchone()

    def recover_reply(self, task: dict, reply, handler):
        row = self._invocation_for_task(task)
        plan = self._row(row["invocation_id"], "invoke_model") if row else None
        if row is None or plan is None:
            raise AuthorityDenied("subscription application lacks durable confined provenance")
        scope = json.loads(plan["scope_json"])
        request, billing = ModelRequest.model_validate(scope["original_request"]), scope["billing"]
        def authorize():
            return handler._eligible(task)
        self._assert_scope(scope, request, billing, authorize, recovery=True)
        completion = self._row(row["invocation_id"], "apply_role_result")
        if completion is not None and completion["response_json"]:
            self._assert_scope(json.loads(completion["scope_json"]), request, billing, authorize, recovery=True)
            payload = json.loads(completion["response_json"])["payload"]
        else:
            result = self._journal_result(row)
            if not result.ok:
                raise ValidationFailure("unresolved subscription result cannot apply departmental effects")
            transformed, _ = self._evaluate("apply_role_result", row["invocation_id"], request, billing, authorize,
                                            result=result.payload, recovery=True)
            payload = transformed["payload"]
        return type(reply).model_validate(payload)
