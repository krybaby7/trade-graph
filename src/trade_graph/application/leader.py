"""Leader routing and Secretary digests. The Leader does not approve trades or raise the owner allowance."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary as Secretary
from trade_graph.contracts.models import Mandate, PauseProfile
from trade_graph.domain.errors import AuthorityDenied


class LeaderOffice:
    def __init__(self, execution: Execution, scheduler: Scheduler, budget: BudgetGateway) -> None:
        self.execution = execution
        self.scheduler = scheduler
        self.budget = budget

    def set_pause(self, portfolio_id: str, profile: PauseProfile, reason: str) -> None:
        pause = self.execution.pause(portfolio_id)
        if pause and pause['profile'] != 'RUNNING' and pause['originator'] != 'leader':
            raise AuthorityDenied('Leader cannot clear or replace another originator pause')
        if profile == 'RUNNING' and pause and pause['originator'] != 'leader':
            raise AuthorityDenied('Leader may resume only its own pauses')
        self.execution.set_pause(portfolio_id, profile, "leader", reason)

    def raise_allowance(self, deployment_id: str, total: Decimal) -> None:
        current = self.budget.allowance(deployment_id)
        if total != current:
            raise AuthorityDenied("leader cannot raise the owner allowance")

    def approve_order(self, portfolio_id: str) -> None:
        del portfolio_id
        raise AuthorityDenied("leader does not approve individual orders")

    def assign(self, *, role: str, objective: str, portfolio_id: str) -> str:
        if role == "owner":
            raise AuthorityDenied("leader cannot assign the owner role")
        return self.scheduler.add_task(role=role, objective=objective, portfolio_id=portfolio_id)

    def set_schedule(self, portfolio_id: str, name: str, interval_seconds: int) -> None:
        self.scheduler.ensure_schedule(portfolio_id, name, interval_seconds, "coalesce")

    def install_mandate(self, mandate: Mandate) -> None:
        self.execution.authority.install_mandate(mandate, role="leader")
