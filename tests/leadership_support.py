"""Credential-free fixtures use real Secretary/worker/gateway paths, not success inserts."""

from decimal import Decimal

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.execution import Execution
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.leader import LeaderOffice, Secretary
from trade_graph.application.leadership import LeaderHandler
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.worker import RoleWorker
from trade_graph.contracts.models import PriceCard


def stack(database, clock, ledger, portfolio):
    seed_paper_authority(database, clock, portfolio)
    budget = BudgetGateway(database, clock)
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("5"),
        period=Decimal("5"),
        priority_reserve=Decimal("1"),
        daily=Decimal("1.50"),
        root=Decimal("1.50"),
        roles={"leader": Decimal("1"), "research": Decimal("1"), "engineer": Decimal("1")},
    )
    budget.seed_card(
        PriceCard(
            price_card_id="scripted-review",
            provider="scripted",
            model="scripted",
            endpoint="scripted",
            currency="EUR",
            input_per_million="0.1",
            output_per_million="0.1",
            effective_at="2026-01-01",
            verified_at="2026-01-01",
            source_id="fixture",
            tier="standard",
            context_band="short",
        )
    )
    execution = Execution(database, ledger, clock, PaperBroker(database, clock))
    scheduler = Scheduler(database, clock)
    office = LeaderOffice(execution, scheduler, budget)
    secretary = Secretary(execution, scheduler)
    gateway = ModelGateway(budget, paid_calls_enabled=False)
    handler = LeaderHandler(office, secretary, gateway, deployment_id="deployment", price_card_id="scripted-review")
    return office, secretary, gateway, handler


def reply(refs, actions=None):
    return {
        "evidence_refs": refs,
        "rationale": "Review the retained departmental evidence.",
        "intended_outcome": "A bounded, reviewable improvement.",
        "review_criteria": "Check independent evidence.",
        "actions": actions or [],
    }


def commission(engineer, portfolio, proposal):
    db, clock = engineer.database, engineer.clock
    office, secretary, gateway, handler = stack(db, clock, engineer.ledger, portfolio)
    engineer.propose(portfolio, proposal)
    ref = secretary.report(
        portfolio,
        role="optimisation",
        kind="proposal",
        summary=proposal.objective,
        evidence_refs=[proposal.record_id],
        source_key=proposal.record_id,
    )
    gateway.scripted.outputs["leader"] = reply([ref], [{"kind": "commission", "change_id": proposal.record_id}])
    task_id = secretary.scheduled(portfolio)
    worker = RoleWorker(
        office.scheduler, owner="fixture-worker", system_version_id=proposal.baseline_hash, reconcile=lambda: None
    )
    assert worker.run_available({"leader": handler}) == 1
    task = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    assert task["status"] == "SUCCEEDED", task["output_json"]
    return office, secretary, gateway, handler, task_id
