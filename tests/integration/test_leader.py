"""Leader routing. No per-trade approval and no owner-budget increase."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import paper_mandate, seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.leader import LeaderOffice, Secretary
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied


def _office(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "leader.sqlite")
    ledger = Ledger(database, clock)
    execution = Execution(database, ledger, clock, PaperBroker(database, clock))
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    seed_paper_authority(database, clock, portfolio)
    budget = BudgetGateway(database, clock)
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("5"),
        period=Decimal("5"),
        priority_reserve=Decimal("1"),
        daily=Decimal("5"),
        root=Decimal("5"),
        roles={"leader": Decimal("1")},
    )
    scheduler = Scheduler(database, clock)
    return execution, LeaderOffice(execution, scheduler, budget), Secretary(execution, scheduler), budget, portfolio


def test_leader_cannot_raise_budget_lift_owner_halt_or_approve_orders(tmp_path) -> None:
    execution, leader, secretary, budget, portfolio = _office(tmp_path)
    before = budget.remaining("deployment")
    with pytest.raises(AuthorityDenied, match="allowance"):
        leader.raise_allowance("deployment", Decimal("50"))
    assert budget.allowance("deployment") == Decimal("5")
    assert budget.remaining("deployment") == before
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "halt")
    with pytest.raises(AuthorityDenied):
        leader.set_pause(portfolio, "RUNNING", "resume")
    assert execution.profile(portfolio) == "MANAGE_ONLY"
    orders = execution.database.execute("SELECT COUNT(*) AS n FROM order_intents").fetchone()["n"]
    with pytest.raises(AuthorityDenied, match="approve"):
        leader.approve_order(portfolio)
    assert execution.database.execute("SELECT COUNT(*) AS n FROM order_intents").fetchone()["n"] == orders
    task_id = leader.assign(role="research", objective="refresh BTC", portfolio_id=portfolio)
    leader.set_schedule(portfolio, "research-daily", 86400)
    digest = secretary.digest(portfolio)
    assert digest["tasks"] == 1
    assert digest["orders_created"] == 0
    assert digest["pause"] == "MANAGE_ONLY"
    assert task_id
    tighter = paper_mandate(portfolio, revision=2, gross="0.40", asset="0.20")
    leader.install_mandate(tighter)
    assert execution.authority.active_mandate(portfolio).revision == 2
    with pytest.raises(AuthorityDenied):
        leader.install_mandate(paper_mandate(portfolio, revision=3, gross="0.95", asset="0.90"))
