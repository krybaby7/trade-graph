"""Six real durable handlers with mutable graph stages confined out of process."""

from __future__ import annotations

from trade_graph.application.runtime_models import RuntimeHandlers, assemble_handlers
from trade_graph.kernel.department_gateway import ProtectedDepartmentGateway
from trade_graph.kernel.protected_budget import ProtectedBudgetGateway


def assemble_protected_handlers(office, secretary, engineer, artifact_runtime, config, *,
                                protected_runtime, workspace_root, api_keys=None, transport=None) -> RuntimeHandlers:
    base = assemble_handlers(office, secretary, engineer, artifact_runtime, config,
                             workspace_root=workspace_root, api_keys=api_keys, transport=transport)
    base.gateway.budget = ProtectedBudgetGateway(base.gateway.budget, protected_runtime)
    gateway = ProtectedDepartmentGateway(base.gateway, protected_runtime)
    gateway.secretary = secretary
    for routed in base.handlers.values():
        build_original = routed.build

        def build(route, original=build_original):
            handler = original(route)
            handler.gateway = gateway
            if handler.provider and not hasattr(handler, "apply"):
                eligible_original = handler._eligible

                def eligible(task):
                    result = eligible_original(task)
                    job = handler._job(task)
                    if job and job["phase"] == "REQUESTING":
                        gateway.assert_engineer_request(task)
                    return result

                handler._eligible = eligible
                terminal_original = handler._terminal

                def terminal(task, job, status, reason, candidate_id=None):
                    with handler.database.immediate():
                        if status == "SUCCEEDED":
                            gateway.assert_application(task)
                        output = terminal_original(task, job, status, reason, candidate_id)
                        if output.get("_status") == "SUCCEEDED":
                            gateway.record_effect(task)
                        return output

                handler._terminal = terminal
            if hasattr(handler, "apply"):
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

        routed.build = build
    return RuntimeHandlers(gateway, base.router, base.handlers)
