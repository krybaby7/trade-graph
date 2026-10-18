"""The installed Perplexity route's protected budget origins (W10-A, D265), like the news route's Jev checks (D258).

Each reservation of the route gets an authenticated protected origin in its own transaction, before anything is
sent; dispatch, settlement, commit and allocation validate it; a receipt seals its commitment with a 100% allocation;
and the route's history is audited, bounded, before each round. A missing or forged origin is refused and recorded,
and its hold stays unresolved: never treated as free.

The protected runtime is real (``ProtectedPaperRuntime`` over the synthetic day-plan account, its controller running);
the owner's folder is simulated; Perplexity answers are T36's fixtures played by a scripted transport with a made-up
key. No network, real key, paid call or order.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.day_plan_support import day_plan_account
from tests.focus_research_support import BTC_NEWS, ScriptedPerplexity, final, search, switch, tool_call
from tests.perplexity_service_support import owner_files, protected_runtime, trust_owner_files

from trade_graph.adapters.persistence.budget_origin_schema import STATEMENTS as ORIGIN_SCHEMA
from trade_graph.application import perplexity_installation as installed
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.web_research import (
    AGENT_CARD_ID,
    SEARCH_CARD_ID,
    PerplexityWebResearch,
    WebResearchAsk,
)
from trade_graph.contracts.models import ModelUsage
from trade_graph.contracts.web_research import PerplexitySpendPolicy
from trade_graph.domain.errors import PaidCallsDisabled, StaleState
from trade_graph.kernel.runtime_manifest import canonical_json

OWNER = Path("/run/trade-graph-owner")
ROUTE_DEPLOYMENT = "deployment:perplexity"
UNLIMITED = PerplexitySpendPolicy(revision_id="synthetic-owner-testing", mode="unlimited_testing",
                                  decision_ref="D125")


@pytest.fixture
def stack(tmp_path, monkeypatch):
    account = day_plan_account(tmp_path)
    switch(account, research=True, leader=True)
    protected = protected_runtime(account)
    trust_owner_files(monkeypatch, owner_files())
    transport = ScriptedPerplexity(account.database)
    route = installed.assemble_perplexity_route(account.database, account.clock, OWNER, deployment_id="deployment",
                                                protected=protected, transport_factory=lambda _profile: transport)
    assert route.problem is None, route.problem
    return SimpleNamespace(account=account, protected=protected, route=route, budget=route.gateway.budget,
                           transport=transport, database=account.database, monkeypatch=monkeypatch)


def _leased_task(account, role: str = "research") -> str:
    scheduler = Scheduler(account.database, account.clock)
    task_id = scheduler.add_task(role=role, objective="wake:focus", portfolio_id=account.portfolio,
                                 allocated_spend=Decimal("1.50"))
    lease = scheduler.claim("synthetic-worker", roles={role})
    assert lease is not None and lease.task_id == task_id
    return task_id


def _reserve(stack, task_id: str, **changes) -> str:
    values = dict(deployment_id=ROUTE_DEPLOYMENT, role="research", task_id=task_id, root_task_id=task_id,
                  price_card_id=AGENT_CARD_ID, max_input=1000, max_output=100, max_tools=0, fx_rate=Decimal("1"),
                  fx_buffer=Decimal("1.02"), priority=False, synthetic=False, purpose="research")
    values.update(changes)
    return stack.budget.reserve(**values)


def _commit(stack, reservation_id: str, request_id: str = "synthetic-request-1") -> str:
    usage = ModelUsage(uncached_input_tokens=400, billed_output_tokens=40, provider_request_id=request_id)
    return stack.budget.commit(reservation_id, usage, provider="perplexity",
                               model=stack.budget.card(AGENT_CARD_ID).model, fx_rate=Decimal("1"))


def _rows(database, sql, *params):
    return [dict(row) for row in database.execute(sql, params).fetchall()]


def _origins(database):
    return _rows(database, "SELECT reservation_id, event_kind, deployment_id, portfolio_id, instance_id "
                           "FROM protected_financial_budget_origins ORDER BY rowid")


def _state(database, reservation_id: str) -> str:
    return database.execute("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                            (reservation_id,)).fetchone()["state"]


# --- origins at reservation, dispatch, settlement and allocation ----------------------------------------------------

def test_each_reservation_gets_its_protected_origin_and_its_receipt_seals_a_full_allocation(stack) -> None:
    account, database = stack.account, stack.database
    task = _leased_task(account)
    reservation = _reserve(stack, task)
    [origin] = _origins(database)
    assert (origin["reservation_id"], origin["event_kind"], origin["deployment_id"], origin["portfolio_id"],
            origin["instance_id"]) == (reservation, "ORIGIN", ROUTE_DEPLOYMENT, account.portfolio,
                                       stack.protected.controller.instance_id)
    stack.budget.protect_dispatch(reservation)
    receipt = _commit(stack, reservation)
    assert [row["event_kind"] for row in _origins(database)] == ["ORIGIN", "RECEIPT"]
    cost = database.execute("SELECT reporting_cost FROM usage_receipts WHERE receipt_id = ?",
                            (receipt,)).fetchone()[0]
    [allocation] = _rows(database, "SELECT portfolio_id, weight, amount FROM cost_allocations WHERE receipt_id = ?",
                         receipt)
    assert allocation == {"portfolio_id": account.portfolio, "weight": "1", "amount": cost}
    stack.budget.allocate(receipt, {account.portfolio: Decimal("1")})  # the gateway's own allocation is the same one
    assert database.execute("SELECT COUNT(*) FROM cost_allocations").fetchone()[0] == 1
    stack.budget.protect_settlement(reservation)
    stack.budget.audit()
    assert stack.route.round_problem(account.clock.now()) is None
    # The subscription deployment's protected books are untouched by the route's records and still verify.
    stack.protected.financial.origins.verify_all()


def test_a_reservation_whose_origin_cannot_be_recorded_is_not_kept(stack) -> None:
    task = _leased_task(stack.account)

    def refuse(*_args, **_kwargs):
        raise StaleState("synthetic: the protected origin could not be recorded")

    stack.monkeypatch.setattr(stack.budget.origins, "record_origin", refuse)
    with pytest.raises(StaleState):
        _reserve(stack, task)
    assert stack.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_a_whole_round_reserves_with_origins_passes_the_audit_and_ends_receipted(stack) -> None:
    account, database = stack.account, stack.database
    task = _leased_task(account)
    stack.transport.add(tool_call("Bitcoin news"), search(BTC_NEWS),
                        final({"summary": "Synthetic: nothing new on BTC.", "findings": []}))
    outcome = PerplexityWebResearch(stack.route.gateway, route=stack.route.route, policy=UNLIMITED).run(
        WebResearchAsk(role="research", portfolio_id=account.portfolio, task_id=task, root_task_id=task,
                       system_version_id="v", question="Synthetic: anything new on BTC?", max_searches=1,
                       max_input_tokens=4000, max_output_tokens=500))
    assert (outcome.status, outcome.billing) == ("completed", "resolved")
    reservations = _rows(database, "SELECT reservation_id, state, price_card_id FROM budget_reservations")
    assert len(reservations) == len(stack.transport.calls) == 3
    assert {row["state"] for row in reservations} == {"COMMITTED"}
    assert {row["price_card_id"] for row in reservations} == {AGENT_CARD_ID, SEARCH_CARD_ID}
    origins = _origins(database)
    assert sorted((row["reservation_id"], row["event_kind"]) for row in origins) == sorted(
        (row["reservation_id"], kind) for row in reservations for kind in ("ORIGIN", "RECEIPT"))
    assert {(row["deployment_id"], row["portfolio_id"]) for row in origins} == {(ROUTE_DEPLOYMENT, account.portfolio)}
    allocations = _rows(database, "SELECT portfolio_id, weight FROM cost_allocations")
    assert allocations == [{"portfolio_id": account.portfolio, "weight": "1"}] * 3  # every receipt, once
    stack.budget.audit()
    assert stack.route.round_problem(account.clock.now()) is None


# --- a missing or forged origin is refused, and its hold stays unresolved --------------------------------------------

def test_a_reservation_without_its_protected_origin_is_never_dispatched_or_settled(stack) -> None:
    task = _leased_task(stack.account)
    plain = _plain_reservation(stack, task)
    with pytest.raises(StaleState):
        stack.budget.protect_dispatch(plain)
    with pytest.raises(StaleState):
        _commit(stack, plain)
    with pytest.raises(StaleState):
        stack.budget.audit()
    assert _state(stack.database, plain) == "RESERVED"  # held at its maximum, never free
    assert stack.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    problem = stack.route.round_problem(stack.account.clock.now())
    assert problem is not None and "protected budget origins were refused" in problem


@contextmanager
def _immutability_removed(database):
    """Someone who can write the database file can drop the origins' immutability triggers (they are restored after);
    the origin's authentication must still refuse whatever they write."""
    with database.immediate() as connection:
        connection.execute("DROP TRIGGER protected_financial_budget_origins_no_update")
        connection.execute("DROP TRIGGER protected_financial_budget_origins_no_delete")
        yield connection
        for statement in ORIGIN_SCHEMA:
            if statement.lstrip().startswith("CREATE TRIGGER"):
                connection.execute(statement)


def _plain_reservation(stack, task: str) -> str:
    """A reservation written on the route's deployment without going through the route, so without its origin (marked
    synthetic only because a plain budget has no allowance to check a real one against)."""
    return BudgetGateway(stack.database, stack.account.clock).reserve(
        deployment_id=ROUTE_DEPLOYMENT, role="research", task_id=task, root_task_id=task,
        price_card_id=AGENT_CARD_ID, max_input=10, max_output=10, max_tools=0, fx_rate=Decimal("1"),
        fx_buffer=Decimal("1.02"), priority=False, synthetic=True, purpose="research")


def _forge(stack, reservation_id: str, how: str) -> str:
    """Tamper with a reservation's protected origin (or the reservation itself); returns the reservation to check."""
    database = stack.database
    if how == "replayed":  # a genuine origin copied onto a reservation written without the route
        target = _plain_reservation(stack, database.execute("SELECT task_id FROM budget_reservations WHERE "
                                                            "reservation_id = ?", (reservation_id,)).fetchone()[0])
        with database.immediate() as connection:
            row = dict(connection.execute("SELECT * FROM protected_financial_budget_origins WHERE reservation_id = ? "
                                          "AND event_kind = 'ORIGIN'", (reservation_id,)).fetchone())
            row.update(origin_id="synthetic-replayed-" + target, reservation_id=target)
            connection.execute(f"INSERT INTO protected_financial_budget_origins ({', '.join(row)}) VALUES "
                               f"({', '.join('?' for _ in row)})", tuple(row.values()))
        return target
    if how == "reservation_amount":  # the reservation itself reinterpreted: a smaller hold would refill the limit
        with database.immediate() as connection:
            connection.execute("UPDATE budget_reservations SET amount = '0' WHERE reservation_id = ?",
                               (reservation_id,))
        return reservation_id
    other = stack.account.ledger.create_portfolio(reporting_currency="EUR") if how == "other_portfolio" else None
    with _immutability_removed(database) as connection:
        if how == "origin_document":  # a rewritten origin keeps its old authentication
            row = connection.execute("SELECT origin_json FROM protected_financial_budget_origins "
                                     "WHERE reservation_id = ? AND event_kind = 'ORIGIN'", (reservation_id,)).fetchone()
            document = json.loads(row["origin_json"])
            document["reservation"]["amount"] = "0"
            connection.execute("UPDATE protected_financial_budget_origins SET origin_json = ? WHERE reservation_id = ?"
                               " AND event_kind = 'ORIGIN'", (canonical_json(document), reservation_id))
        elif how == "authentication":
            connection.execute("UPDATE protected_financial_budget_origins SET authentication = ? "
                               "WHERE reservation_id = ?", ("0" * 64, reservation_id))
        elif how == "deleted":
            connection.execute("DELETE FROM protected_financial_budget_origins WHERE reservation_id = ?",
                               (reservation_id,))
        else:
            connection.execute("UPDATE protected_financial_budget_origins SET portfolio_id = ? "
                               "WHERE reservation_id = ?", (other, reservation_id))
    return reservation_id


@pytest.mark.parametrize("how", ["origin_document", "authentication", "deleted", "other_portfolio", "replayed",
                                 "reservation_amount"])
def test_a_forged_origin_is_refused_at_dispatch_settlement_and_by_the_audit(stack, how) -> None:
    task = _leased_task(stack.account)
    reservation = _forge(stack, _reserve(stack, task), how)
    with pytest.raises(StaleState):
        stack.budget.protect_dispatch(reservation)
    with pytest.raises(StaleState):
        _commit(stack, reservation)
    with pytest.raises(StaleState):
        stack.budget.protect_settlement(reservation)
    with pytest.raises(StaleState):
        stack.budget.audit()
    assert _state(stack.database, reservation) == "RESERVED"  # left unresolved, never settled as free
    assert stack.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    problem = stack.route.round_problem(stack.account.clock.now())
    assert problem is not None and "protected budget origins were refused" in problem


def test_an_origin_lost_before_dispatch_stops_the_round_before_anything_is_sent(stack) -> None:
    account, database, budget = stack.account, stack.database, stack.budget
    task = _leased_task(account)
    original = budget.protect_dispatch

    def lose_then_check(reservation_id: str) -> None:  # another writer removes the origin after the reservation
        with _immutability_removed(database) as connection:
            connection.execute("DELETE FROM protected_financial_budget_origins WHERE reservation_id = ?",
                               (reservation_id,))
        original(reservation_id)

    stack.monkeypatch.setattr(budget, "protect_dispatch", lose_then_check)
    stack.transport.add(tool_call("Bitcoin news"))
    with pytest.raises(StaleState):  # FocusResearch and LeaderNewsRounds record this as a failed round
        PerplexityWebResearch(stack.route.gateway, route=stack.route.route, policy=UNLIMITED).run(WebResearchAsk(
            role="research", portfolio_id=account.portfolio, task_id=task, root_task_id=task, system_version_id="v",
            question="Synthetic: anything new on BTC?", max_searches=1, max_input_tokens=4000,
            max_output_tokens=500))
    assert stack.transport.calls == []
    [hold] = _rows(database, "SELECT reservation_id, state, amount FROM budget_reservations")
    assert hold["state"] == "RESERVED" and Decimal(hold["amount"]) > 0  # held at its maximum, never free
    problem = stack.route.round_problem(account.clock.now())
    assert problem is not None and "protected budget origins were refused" in problem


def test_a_settled_amount_changed_later_is_found_by_the_round_audit(stack) -> None:
    task = _leased_task(stack.account)
    reservation = _reserve(stack, task)
    _commit(stack, reservation)
    assert stack.route.round_problem(stack.account.clock.now()) is None
    with stack.database.immediate() as connection:
        connection.execute("UPDATE budget_reservations SET amount = '0' WHERE reservation_id = ?", (reservation,))
    problem = stack.route.round_problem(stack.account.clock.now())
    assert problem is not None and "protected budget origins were refused" in problem


def test_a_changed_reservation_from_earlier_this_month_is_found_by_the_rolling_sweep(stack) -> None:
    task = _leased_task(stack.account)
    reservation = _reserve(stack, task)
    _commit(stack, reservation)
    stack.account.clock.advance(timedelta(days=1).total_seconds())  # yesterday's settled call is outside today's audit
    with stack.database.immediate() as connection:
        connection.execute("UPDATE budget_reservations SET amount = '0' WHERE reservation_id = ?", (reservation,))
    with pytest.raises(StaleState):
        stack.budget.audit(sweep_since="2026-10-01T00:00:00.000000Z")
    problem = stack.route.round_problem(stack.account.clock.now())
    assert problem is not None and "protected budget origins were refused" in problem


# --- the round check in the installed service -----------------------------------------------------------------------

def _runtime(account, protected):
    return SimpleNamespace(database=account.database, clock=account.clock, execution=account.execution,
                           ledger=account.ledger, portfolio_id=account.portfolio, deployment_id="deployment",
                           protected_deployment=SimpleNamespace(protected=protected))


class _Subscription:
    def __init__(self):
        self.calls = []

    def context(self, task):
        return {"input": task["input"]}

    def recover(self, task):
        return None

    def __call__(self, task):
        self.calls.append(task)
        return {"summary": "subscription answer"}


def _activities(database):
    return [json.loads(row["payload_json"]) for row in database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = 'perplexity_route' ORDER BY rowid").fetchall()]


def test_a_round_refused_by_the_audit_fails_its_wake_with_the_reason_records_it_and_sends_nothing(stack) -> None:
    account, database = stack.account, stack.database
    handlers = {"research": _Subscription(), "leader": _Subscription()}
    stack.monkeypatch.setattr(installed, "pinned_transport", lambda _profile: stack.transport)
    departments = installed.perplexity_departments(_runtime(account, stack.protected), OWNER, handlers,
                                                   engine=object())
    assert _activities(database)[-1]["status"] == "ready"
    route_budget = departments["research"].installation.gateway.budget
    task = _leased_task(account)
    reservation = route_budget.reserve(
        deployment_id=ROUTE_DEPLOYMENT, role="research", task_id=task, root_task_id=task,
        price_card_id=AGENT_CARD_ID, max_input=1000, max_output=100, max_tools=0, fx_rate=Decimal("1"),
        fx_buffer=Decimal("1.02"), priority=False, synthetic=False, purpose="research")
    _forge(stack, reservation, "origin_document")
    wake = {"task_id": "synthetic-wake", "input": {"wake": {"purpose": "focus"}}}
    wake["snapshot"] = {"department_route": "perplexity", "input": wake["input"]}  # the turn's route, as recorded
    output = departments["research"](wake)
    assert output["_status"] == "FAILED" and output["failure_scope"] == "turn"
    assert "protected budget origins were refused" in output["reason"] and "Nothing was sent" in output["reason"]
    refused = _activities(database)[-1]
    assert (refused["status"], refused["department"], refused["task_id"]) == ("refused", "research", "synthetic-wake")
    assert "protected budget origins were refused" in refused["reason"]
    assert stack.transport.calls == [] and handlers["research"].calls == []
    assert _state(database, reservation) == "RESERVED"


def test_the_route_waits_while_the_protected_controller_is_not_running(stack) -> None:
    task = _leased_task(stack.account)
    stack.monkeypatch.setattr(stack.protected.controller, "status", lambda: {"status": "MANAGEMENT_ONLY"})
    with pytest.raises(PaidCallsDisabled, match="management only"):
        _reserve(stack, task)
    assert stack.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    problem = stack.route.round_problem(stack.account.clock.now())
    assert problem is not None and "management only" in problem


def test_without_the_protected_runtime_the_route_is_not_ready_and_nothing_is_built(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    switch(account, research=True, leader=True)
    trust_owner_files(monkeypatch, owner_files())
    built = []
    route = installed.assemble_perplexity_route(account.database, account.clock, OWNER, deployment_id="deployment",
                                                protected=None, transport_factory=built.append)
    assert route.problem is not None and "protected runtime is not prepared" in route.problem
    assert route.gateway is None and built == []


def test_a_route_budget_is_bound_to_its_own_scope_and_financial_service(stack) -> None:
    from trade_graph.kernel.protected_budget import ProtectedBudgetOrigins

    financial, controller = stack.protected.financial, stack.protected.controller
    for scope in (None, "deployment:news_alarm"):  # the subscription deployment's own scope, or another route's
        with pytest.raises(ValueError):
            installed.PerplexityRouteBudget(stack.database, stack.account.clock, deployment_id=ROUTE_DEPLOYMENT,
                                            origins=ProtectedBudgetOrigins(financial, deployment_id=scope),
                                            controller=controller)
