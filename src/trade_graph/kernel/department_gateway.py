"""Confined mutable reasoning around the real protected six-role model gateway.

The source chooses bounded reasoning text and a typed role result. Credentials,
provider/card/schema/limits, durable effects, leases and receipts stay in parent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sys
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.application.gateway import _bounded_json, _validate_schema
from trade_graph.application.model_invocations import InvocationJournal
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError, ValidationFailure
from trade_graph.kernel.process_boundary import _bounded_number, _no_duplicate_keys
from trade_graph.kernel.runtime_manifest import canonical_json, document_sha256

CHILD = Path(__file__).resolve().parents[1] / "adapters" / "isolation" / "department_child.py"
ROLES = {"research", "trader", "learning", "optimisation", "leader", "engineer"}
PUBLIC_FIELDS = {"objective", "reports", "proposals", "candidates", "evidence_refs", "consultation_result",
                 "source_instruction", "market", "portfolio", "selected_context", "strategy_templates",
                 "research", "as_of", "artifact", "change", "source_files", "previous_findings",
                 "sources", "decisions", "lesson_revisions", "analysis_instruction", "expenses", "task_counts"}
FORBIDDEN_CONTEXT_KEYS = {"guard", "budget", "roles", "api_keys", "credential", "credentials", "owner_policy", "policy",
                          "deployment_budget", "http_fixture", "scripted", "tools", "tool_results", "temperature",
                          "forced_tool", "model_route", "max_input_tokens"}
FINANCIAL_FIELDS = {"amount", "quantity", "max_gross_exposure_fraction", "max_single_asset_exposure_fraction",
                    "max_spend_eur"}


def bounded_financial_payload(value, key=None) -> None:
    """Reject expansion/precision attacks before original typed Decimal parsing."""
    if key in FINANCIAL_FIELDS and value is not None and type(value) is not dict:
        raw = str(value) if type(value) is int else value
        normalized = _bounded_number(raw, Decimal("1000000000000"))
        if len(Decimal(normalized).as_tuple().digits) > 28:
            raise ValidationFailure("mutable native amount exceeds protected precision")
    if type(value) is dict:
        for name, child in value.items():
            bounded_financial_payload(child, name)
    elif type(value) is list:
        for child in value:
            bounded_financial_payload(child)


def public_context(value, depth=0):
    """Remove protected owner/billing/provider controls before child serialization."""
    if depth > 20:
        raise ValidationFailure("departmental context nesting exceeded")
    if type(value) is dict:
        return {key: public_context(item, depth + 1) for key, item in value.items()
                if key not in FORBIDDEN_CONTEXT_KEYS}
    if type(value) is list:
        if len(value) > 512:
            raise ValidationFailure("departmental context collection exceeded")
        return [public_context(item, depth + 1) for item in value]
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise ValidationFailure("departmental context requires serializable bounded data")


class ProtectedDepartmentGateway:
    def __init__(self, gateway, protected_runtime) -> None:
        self.gateway, self.runtime = gateway, protected_runtime
        self.financial, self.controller = protected_runtime.financial, protected_runtime.controller
        self.database, self.clock = self.financial.database, self.financial.clock
        self.manifest = self.financial.manifest
        if (gateway.budget.database is not self.database or gateway.budget.clock is not self.clock
                or gateway.router.config.deployment_id != self.manifest.deployment_id
                or self.manifest.operations != ("submit_decision", "invoke_model", "apply_role_result")):
            raise AuthorityDenied("protected department gateway requires exact opt-in deployment binding")
        self.cancelled = threading.Event()

    def __getattr__(self, name):
        # Used only by trusted deterministic role handlers, never exposed to child.
        return getattr(self.gateway, name)

    def _scope(self, request: ModelRequest, billing: dict) -> dict:
        instance = self.financial._instance(self.controller.instance_id)
        if instance["status"] != "RUNNING":
            raise AuthorityDenied("protected departmental graph is management-only")
        release = self.financial._release(instance)
        task = self.database.execute("SELECT * FROM tasks WHERE task_id=?", (request.task_id,)).fetchone()
        if (task is None or task["role"] != request.role or task["root_task_id"] != request.root_task_id
                or task["portfolio_id"] != billing["portfolio_id"] or task["status"] not in {"LEASED", "RUNNING"}
                or request.role not in ROLES or not task["lease_token"]):
            raise AuthorityDenied("departmental RPC is outside its persisted leased task")
        policy = self.financial.authority.active_policy()
        mandate = self.financial.authority.active_mandate(task["portfolio_id"])
        active = self.database.execute("SELECT * FROM active_versions WHERE portfolio_id=?",
                                       (task["portfolio_id"],)).fetchone()
        budget = self.database.execute("SELECT * FROM deployment_budget WHERE deployment_id=?",
                                       (self.manifest.deployment_id,)).fetchone()
        roles = [dict(row) for row in self.database.execute("SELECT * FROM role_allocations WHERE deployment_id=? "
                 "ORDER BY role", (self.manifest.deployment_id,))]
        return {"manifest_sha256": self.manifest.sha256, "instance_id": self.controller.instance_id,
                "release_id": release["release_id"], "source_sha256": release["source_sha256"],
                "build_digest": release["build_digest"], "generation": instance["generation"],
                "task_id": task["task_id"], "role": task["role"], "root_task_id": task["root_task_id"],
                "portfolio_id": task["portfolio_id"], "lease_owner": task["lease_owner"],
                "lease_token": task["lease_token"], "snapshot_id": request.run_id,
                "system_version_id": request.system_version_id, "request_sha256": document_sha256(
                    request.model_dump(mode="json")), "billing_sha256": document_sha256(billing),
                "guard_sha256": document_sha256({"policy": policy.model_dump(mode="json"),
                    "mandate": mandate.model_dump(mode="json"), "active": dict(active) if active else None,
                    "budget": dict(budget) if budget else None, "roles": roles,
                    "model_config": self.gateway.router.config.model_dump(mode="json"),
                    "price_card": self.gateway.budget.card(billing["price_card_id"]).model_dump(mode="json"),
                    "pause": self.financial.execution.pause(task["portfolio_id"]),
                    "allocated_spend": task["allocated_spend"], "attempts_used": task["attempts_used"]})}

    def _row(self, invocation_id: str, operation: str):
        return self.database.execute("""SELECT * FROM protected_rpc_requests WHERE instance_id=?
            AND json_extract(scope_json,'$.invocation_id')=? AND json_extract(scope_json,'$.operation')=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (self.controller.instance_id, invocation_id, operation)).fetchone()

    def _assert_scope(self, stored: dict, request: ModelRequest, billing: dict, authorize, *, recovery=False) -> None:
        self.manifest.assert_current()
        if self.cancelled.is_set():
            raise AuthorityDenied("protected departmental work was cancelled")
        authorize()
        current = self._scope(request, billing)
        if any(stored.get(key) != value for key, value in current.items()
               if not recovery or key not in {"lease_owner", "lease_token"}):
            raise StaleState("departmental role/release/lease/authority/budget scope changed")

    def _recover_failed_mutable(self, scope: dict, request: ModelRequest, billing: dict, authorize) -> None:
        """Independent release recovery; never erase receipts/effects or call child code."""
        with self.database.immediate():
            try:
                self._assert_scope(scope, request, billing, authorize)
            except (TradeGraphError, PermissionError):
                # Cancellation and newer lease/release/owner policy are stronger
                # authority than a delayed failure from the old process.
                return
            self.controller._recover_mutable(scope["release_id"], scope["generation"])

    def _evaluate(self, operation: str, invocation_id: str, request: ModelRequest, billing: dict,
                  authorize, *, result: dict | None = None, recovery=False) -> tuple[dict, dict]:
        self.manifest.assert_current()
        if operation not in self.manifest.operations:
            raise AuthorityDenied("departmental RPC operation is not owner-allowlisted")
        with self.database.immediate():
            authorize()
            scope = {**self._scope(request, billing), "operation": operation, "invocation_id": invocation_id,
                     "original_request": request.model_dump(mode="json"), "billing": billing}
            if result is not None:
                scope["result_sha256"] = document_sha256(result)
            old = self._row(invocation_id, operation)
            if old is not None:
                previous = json.loads(old["scope_json"])
                self._assert_scope(previous, request, billing, authorize, recovery=recovery)
                keys = scope.keys() - ({"lease_owner", "lease_token"} if recovery else set())
                if any(previous.get(key) != scope[key] for key in keys):
                    raise StaleState("departmental invocation key changed its protected request/result")
                if old["response_json"]:
                    return json.loads(old["response_json"]), previous
                if not recovery:
                    raise StaleState("interrupted mutable stage requires explicit task recovery")
                self.financial.revoke(old["request_id"])
            request_id = secrets.token_hex(24)
            scope.update(request_id=request_id, expires_at=utc_iso(self.clock.now() + timedelta(
                seconds=self.manifest.capability_ttl_seconds)))
            capability = hmac.new(self.financial._capability_key, canonical_json(scope).encode(),
                                  hashlib.sha256).hexdigest()
            now = utc_iso(self.clock.now())
            self.database.execute("""INSERT INTO protected_rpc_requests
                (request_id,instance_id,capability_sha256,scope_json,expires_at,state,created_at,updated_at)
                VALUES (?,?,?,?,?,'ISSUED',?,?)""", (request_id, self.controller.instance_id,
                hashlib.sha256(capability.encode()).hexdigest(), canonical_json(scope), scope["expires_at"], now, now))
            release = self.database.execute("SELECT source_text FROM protected_mutable_releases WHERE release_id=?",
                                            (scope["release_id"],)).fetchone()
        try:
            normalized_context = request.model_dump(mode="json")["context"]
            _bounded_json(normalized_context)
            context = {"operation": operation, "role": request.role, "request_id": request_id,
                       "capability": capability, "snapshot": public_context({key: value for key, value in
                           normalized_context.items() if key in PUBLIC_FIELDS}),
                       "instructions": request.instructions, "release_id": scope["release_id"],
                       "source_sha256": scope["source_sha256"], "snapshot_id": request.run_id}
            if result is not None:
                context["result"] = result
            payload = canonical_json({"source": release[0], "context": context}).encode()
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            self.financial.revoke(request_id)
            raise ValidationFailure("departmental context violates protected serialization bounds") from exc
        if len(payload) > 131072:
            self.financial.revoke(request_id)
            raise ValidationFailure("protected departmental serialized input exceeded")
        process = run_bounded([sys.executable, "-I", "-S", "-B", str(CHILD)], payload,
                              cwd=str(CHILD.parent), wall_seconds=self.manifest.wall_seconds)
        mutable_failure = True
        try:
            if process["exit_code"] != 0:
                raise ValidationFailure("confined departmental graph failed")
            if len(process["stdout"].encode()) > self.manifest.maximum_output_bytes:
                raise ValidationFailure("bounded departmental response exceeded")
            envelope = json.loads(process["stdout"], object_pairs_hook=_no_duplicate_keys)
            if (type(envelope) is not dict or set(envelope) != {"request_id", "capability", "operation", "proposal"}
                    or envelope["request_id"] != request_id or envelope["operation"] != operation
                    or type(envelope["capability"]) is not str
                    or not hmac.compare_digest(envelope["capability"], capability)):
                raise AuthorityDenied("departmental child capability/envelope mismatch")
            proposal = envelope["proposal"]
            if operation == "invoke_model":
                if (type(proposal) is not dict or set(proposal) != {"node", "guidance"}
                        or proposal["node"] != "invoke_model" or type(proposal["guidance"]) is not str
                        or len(proposal["guidance"].encode()) > 2000):
                    raise AuthorityDenied("mutable graph cannot choose protected provider/request controls")
            else:
                if (type(proposal) is not dict or set(proposal) != {"node", "payload"}
                        or proposal["node"] != "apply_role_result" or type(proposal["payload"]) is not dict):
                    raise AuthorityDenied("mutable graph cannot choose protected result/effect controls")
                try:
                    _validate_schema(proposal["payload"], request.output_schema)
                    bounded_financial_payload(proposal["payload"])
                except Exception as exc:
                    raise ValidationFailure("mutable result violates protected original role schema") from exc
            mutable_failure = False
            with self.database.immediate():
                self._assert_scope(scope, request, billing, authorize)
                row = self.database.execute("SELECT * FROM protected_rpc_requests WHERE request_id=?",
                                            (request_id,)).fetchone()
                if row["state"] != "ISSUED" or self.clock.now() >= parse_utc(scope["expires_at"]):
                    raise StaleState("departmental capability expired/revoked")
                self.database.execute("UPDATE protected_rpc_requests SET state='APPLIED', request_sha256=?, "
                    "response_json=?,updated_at=? WHERE request_id=?", (document_sha256(proposal),
                    canonical_json(proposal), utc_iso(self.clock.now()), request_id))
            return proposal, scope
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            self.financial.revoke(request_id)
            if mutable_failure:
                self._recover_failed_mutable(scope, request, billing, authorize)
            raise ValidationFailure("confined departmental response violates protected parsing bounds") from exc
        except BaseException as exc:
            self.financial.revoke(request_id)
            if mutable_failure and isinstance(exc, (AuthorityDenied, ValidationFailure)):
                self._recover_failed_mutable(scope, request, billing, authorize)
            raise

    def invoke(self, request: ModelRequest, **kwargs) -> ModelResult:
        invocation_id, authorize = kwargs.get("invocation_id"), kwargs.get("authorize")
        if not invocation_id or not callable(authorize) or request.max_tool_calls != 0:
            raise AuthorityDenied("protected models require durable tool-free leased invocation")
        billing = {name: str(kwargs.get(name)) for name in (
            "deployment_id", "price_card_id", "fx_rate", "fx_buffer", "priority", "attempt_kind", "portfolio_id")}
        old = self._row(invocation_id, "invoke_model")
        if old is not None:
            original_scope = json.loads(old["scope_json"])
            if (document_sha256(request.model_dump(mode="json")) != original_scope["request_sha256"]
                    or document_sha256(billing) != original_scope["billing_sha256"]):
                raise StaleState("invocation key reused with a different protected request/billing")
        with self.database.immediate():
            dispatched = self.database.execute("SELECT * FROM model_invocations WHERE invocation_id=?",
                                               (invocation_id,)).fetchone()
            recovered = InvocationJournal(self.gateway.budget).recover(invocation_id, dispatched["request_hash"]) \
                if dispatched else None
        if recovered is not None and not recovered.ok:
            return recovered
        if old is not None:
            scope = json.loads(old["scope_json"])
            self._assert_scope(scope, request, billing, authorize, recovery=dispatched is not None)
            plan = json.loads(old["response_json"]) if old["response_json"] else None
            if plan is None:
                raise StaleState("interrupted departmental plan cannot implicitly redispatch")
        else:
            plan, scope = self._evaluate("invoke_model", invocation_id, request, billing, authorize)
        effective = request.model_copy(update={"instructions": request.instructions +
            "\nBounded mutable departmental guidance (never authority):\n" + plan["guidance"]})
        context = dict(effective.context)
        context["max_input_tokens"] = int(context.get("max_input_tokens", 0)) + len(plan["guidance"].encode()) + 200
        effective = effective.model_copy(update={"context": context})

        def protected_authorize():
            self._assert_scope(scope, request, billing, authorize)
            if self.clock.now() >= parse_utc(scope["expires_at"]):
                raise StaleState("model dispatch capability expired before provider effect")

        # The real gateway reserves and records dispatch before external effects.
        # Its invocation key never retries an uncertain or already-received call.
        result = recovered or self.gateway.invoke(effective, **{**kwargs, "authorize": protected_authorize})
        if not result.ok:
            return result
        invocation = self.database.execute("SELECT state FROM model_invocations WHERE invocation_id=?",
                                           (invocation_id,)).fetchone()
        if invocation is not None and invocation["state"] == "UNCERTAIN":
            return result
        self._assert_scope(scope, request, billing, authorize, recovery=dispatched is not None)
        transformed, _ = self._evaluate("apply_role_result", invocation_id, request, billing,
                                         authorize, result=result.payload, recovery=dispatched is not None)
        return result.model_copy(update={"payload": transformed["payload"]})

    def recover_reply(self, task: dict, reply, handler):
        row = self._invocation_for_task(task)
        if row is None:
            raise AuthorityDenied("protected application requires durable model provenance")
        planned = self._row(row["invocation_id"], "invoke_model")
        if planned is None:
            raise AuthorityDenied("protected application lacks authenticated confined graph plan")
        scope = json.loads(planned["scope_json"])
        # Reconstruct the unmodified trusted request/billing identity saved by the
        # real handler, rather than fabricating a request from candidate fields.
        request = ModelRequest.model_validate(scope["original_request"])
        billing = scope["billing"]
        def authorize():
            handler._eligible(task)
        self._assert_scope(scope, request, billing, authorize, recovery=True)
        completed = self._row(row["invocation_id"], "apply_role_result")
        if completed is not None and completed["response_json"]:
            self._assert_scope(json.loads(completed["scope_json"]), request, billing, authorize, recovery=True)
            payload = json.loads(completed["response_json"])["payload"]
        else:
            with self.database.immediate():
                result = InvocationJournal(self.gateway.budget).recover(row["invocation_id"], row["request_hash"])
            if result is None or not result.ok:
                raise ValidationFailure("unresolved model result cannot apply departmental effects")
            transformed, _ = self._evaluate("apply_role_result", row["invocation_id"], request, billing,
                                            authorize, result=result.payload, recovery=True)
            payload = transformed["payload"]
        return type(reply).model_validate(payload)

    def assert_application(self, task: dict) -> None:
        row = self._invocation_for_task(task)
        if row is None:
            raise AuthorityDenied("protected application has no durable invocation")
        plan = self._row(row["invocation_id"], "invoke_model")
        completion = self._row(row["invocation_id"], "apply_role_result")
        if plan is None or completion is None or completion["state"] != "APPLIED":
            raise AuthorityDenied("protected application lacks a validated confined completion")
        scope = json.loads(plan["scope_json"])
        self._assert_scope(scope, ModelRequest.model_validate(scope["original_request"]), scope["billing"],
                           lambda: None, recovery=True)

    def record_effect(self, task: dict) -> None:
        """Acknowledge a real successful effect inside its parent's writer transaction.

        A child plan or schema-valid result is insufficient as a rollback baseline.
        This private marker records the exact role result committed with effects.
        """
        invocation = self._invocation_for_task(task)
        completion = self._row(invocation["invocation_id"], "apply_role_result") if invocation else None
        result = self.database.execute("""SELECT * FROM role_results
            WHERE task_id=? AND portfolio_id=? AND role=?""",
            (task["task_id"], task["portfolio_id"], task["role"])).fetchone()
        if completion is None or completion["state"] != "APPLIED" or result is None or result["status"] != "SUCCEEDED":
            raise AuthorityDenied("successful protected role effect lacks exact completion/result provenance")
        response = json.loads(completion["response_json"])
        response["protected_effect"] = {"status": "SUCCEEDED", "document_json": result["document_json"]}
        self.database.execute("UPDATE protected_rpc_requests SET response_json=?,updated_at=? WHERE request_id=?",
            (canonical_json(response), utc_iso(self.clock.now()), completion["request_id"]))

    def _invocation_for_task(self, task: dict):
        invocation_id = f"{task['role']}:{task['task_id']}"
        if task["role"] == "engineer":
            job = self.database.execute("SELECT document_json FROM engineering_jobs WHERE task_id=?",
                                        (task["task_id"],)).fetchone()
            if job is None:
                return None
            invocation_id = json.loads(job[0]).get("invocation_id")
        return self.database.execute("""SELECT * FROM model_invocations WHERE invocation_id=? AND task_id=?
            AND portfolio_id=? AND root_task_id=?""",
            (invocation_id, task["task_id"], task["portfolio_id"], task["root_task_id"])).fetchone()

    def assert_engineer_request(self, task: dict) -> None:
        row = self._invocation_for_task(task)
        if row is None:
            return
        plan = self._row(row["invocation_id"], "invoke_model")
        if plan is None:
            raise AuthorityDenied("Engineer request lacks confined graph provenance")
        scope = json.loads(plan["scope_json"])
        self._assert_scope(scope, ModelRequest.model_validate(scope["original_request"]), scope["billing"],
                           lambda: None, recovery=True)
