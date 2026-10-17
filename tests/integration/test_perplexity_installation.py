"""The installed Perplexity route (W8-A, D240-D244): owner files, pinned transport, prices, money and the switch.

The owner's protected folder is simulated (``tests/perplexity_service_support.py``); the account is the synthetic
day-plan account; Perplexity answers are the T36 fixtures played by a scripted transport with a made-up key. No
network, real key, paid call or order.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.day_plan_support import day_plan_account
from tests.focus_research_support import ScriptedPerplexity, switch
from tests.perplexity_service_support import CARDS, KEY, owner_files, trust_owner_files

from trade_graph.adapters.models.perplexity import AGENT_ENDPOINT, search_tool_definition
from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT
from trade_graph.application import perplexity_installation as installed
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.web_research import AGENT_CARD_ID, SEARCH_CARD_ID
from trade_graph.contracts.models import ModelRequest, PriceCard
from trade_graph.domain.errors import AuthorityDenied, PaidCallsDisabled

OWNER = Path("/run/trade-graph-owner")


def _account(tmp_path, *, research=True, leader=True):
    account = day_plan_account(tmp_path)
    if research or leader:
        switch(account, research=research, leader=leader)
    return account


def _assemble(account, monkeypatch, files=None, transport=None):
    reads = trust_owner_files(monkeypatch, files if files is not None else owner_files())
    built = []

    def factory(profile):
        built.append(profile)
        return transport if transport is not None else ScriptedPerplexity(account.database)

    route = installed.assemble_perplexity_route(account.database, account.clock, OWNER, deployment_id="deployment",
                                                transport_factory=factory)
    return route, reads, built


def _leased_task(account, role: str = "research") -> str:
    scheduler = Scheduler(account.database, account.clock)
    task_id = scheduler.add_task(role=role, objective="wake:focus", portfolio_id=account.portfolio,
                                 allocated_spend=Decimal("1.50"))
    lease = scheduler.claim("synthetic-worker", roles={role})
    assert lease is not None and lease.task_id == task_id
    return task_id


def _reserve(route, task_id: str, *, role: str = "research", card: str = AGENT_CARD_ID, **changes):
    values = dict(deployment_id=route.route.deployment_id, role=role, task_id=task_id, root_task_id=task_id,
                  price_card_id=card, max_input=1000, max_output=100, max_tools=0, fx_rate=Decimal("1"),
                  fx_buffer=Decimal("1.02"), priority=False, synthetic=False, purpose=role)
    values.update(changes)
    return route.gateway.budget.reserve(**values)


# --- assembly -------------------------------------------------------------------------------------------------------

def test_the_route_reads_the_owner_folder_and_pins_the_transport_to_the_reviewed_route(tmp_path, monkeypatch):
    account = _account(tmp_path)
    route, reads, built = _assemble(account, monkeypatch)
    assert route.problem is None and route.problem_now(account.clock.now().date()) is None
    assert reads == ["subscription-network-profile.json", "perplexity.key", "perplexity-price-cards.json"]
    assert route.route.deployment_id == "deployment:perplexity"
    assert (route.route.agent_price_card_id, route.route.search_price_card_id) == (AGENT_CARD_ID, SEARCH_CARD_ID)
    assert route.gateway.paid_calls_enabled and route.gateway.enabled_providers == {"perplexity"}
    # The real transport: both fixed endpoints only, through the reviewed route's port, no environment proxy.
    real = installed.pinned_transport(built[0])
    assert isinstance(real, HttpxProviderHttp)
    assert real.proxy == "http://172.29.17.2:8082" and real.trust_env is False
    assert real.allowed_urls == (AGENT_ENDPOINT, SEARCH_ENDPOINT)
    # The dated cards are seeded as immutable records; the key is nowhere in the database.
    seeded = {row[0] for row in account.database.execute("SELECT price_card_id FROM price_cards")}
    assert {AGENT_CARD_ID, SEARCH_CARD_ID} <= seeded
    dump = "\n".join(account.database.connection.iterdump())
    assert KEY not in dump and KEY not in json.dumps(route.status(account.clock.now().date()))


@pytest.mark.parametrize(("files", "reason"), [
    (owner_files(routes=None), "lists no reviewed paid_research route"),
    (owner_files(key=None), "no Perplexity key"),
    (owner_files(key="short"), "key file is refused"),
    (owner_files(key="spaces are not a key 00000000"), "key file is refused"),
    (owner_files(cards=None), "no Perplexity price cards"),
    (owner_files(cards=b'{"schema_version": 2, "price_cards": []}'), "price-card file is refused"),
    (owner_files(cards=b'{"schema_version": 1, "price_cards": []}'), "no effective Perplexity price card"),
    ({}, "network profile can't be read"),
])
def test_each_missing_prerequisite_is_a_plain_reason_and_no_transport_is_built(tmp_path, monkeypatch, files, reason):
    account = _account(tmp_path)
    route, _reads, built = _assemble(account, monkeypatch, files)
    assert route.problem is not None and reason in route.problem
    assert route.gateway is None and built == []
    assert KEY not in route.problem


def test_an_edited_price_record_is_refused_never_replaced(tmp_path, monkeypatch):
    account = _account(tmp_path)
    document = json.loads(CARDS)
    _assemble(account, monkeypatch)  # seeds the dated cards
    document["price_cards"][0]["input_per_million"] = "0.01"  # same id, cheaper: an edit, not a new dated card
    route, _reads, built = _assemble(account, monkeypatch, owner_files(cards=json.dumps(document).encode()))
    assert route.problem is not None and "immutable" in route.problem and built == []


def test_the_newest_effective_card_per_endpoint_is_used(tmp_path, monkeypatch):
    account = _account(tmp_path)
    document = json.loads(CARDS)
    newer = dict(document["price_cards"][0], price_card_id="perplexity-agent-claude-sonnet-5-5-2026-10-17",
                 verified_at="2026-10-17", effective_at="2026-10-17", input_per_million="3")
    future = dict(document["price_cards"][1], price_card_id="perplexity-search-api-2027-01-01",
                  verified_at="2027-01-01", effective_at="2027-01-01")
    document["price_cards"] += [newer, future]
    account.clock.advance(timedelta(days=2).total_seconds())
    route, _reads, _built = _assemble(account, monkeypatch, owner_files(cards=json.dumps(document).encode()))
    assert route.problem is None
    assert route.route.agent_price_card_id == "perplexity-agent-claude-sonnet-5-5-2026-10-17"
    assert route.route.search_price_card_id == SEARCH_CARD_ID  # the 2027 card is not effective yet


# --- prices ---------------------------------------------------------------------------------------------------------

def test_a_card_goes_stale_thirty_days_after_it_was_checked_and_then_refuses_paid_calls(tmp_path, monkeypatch):
    agent = PriceCard.model_validate(json.loads(CARDS)["price_cards"][0])
    assert agent.verified_at == "2026-10-12"
    assert installed.card_problem(agent, date(2026, 11, 10)) is None
    stale = installed.card_problem(agent, date(2026, 11, 11))
    assert stale is not None and "went stale on 2026-11-11" in stale and "refused" in stale
    account = _account(tmp_path)
    route, _reads, _built = _assemble(account, monkeypatch)
    task = _leased_task(account)
    now = account.clock.now()
    account.clock.advance((datetime(2026, 11, 11, 0, 0, 1, tzinfo=UTC) - now).total_seconds())
    assert "went stale" in route.problem_now(account.clock.now().date())
    with pytest.raises(PaidCallsDisabled, match="went stale"):
        _reserve(route, task)
    assert account.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


# --- money and authority --------------------------------------------------------------------------------------------

def test_the_owner_spend_rule_not_the_eur_allowance_governs_and_the_task_allocation_still_holds(tmp_path,
                                                                                               monkeypatch):
    account = _account(tmp_path)
    route, _reads, _built = _assemble(account, monkeypatch)
    assert account.database.execute("SELECT COUNT(*) FROM deployment_budget").fetchone()[0] == 0  # no EUR allowance
    task = _leased_task(account)
    reservation = _reserve(route, task)
    row = account.database.execute("SELECT * FROM budget_reservations WHERE reservation_id = ?",
                                   (reservation,)).fetchone()
    assert (row["deployment_id"], row["state"], row["synthetic"]) == ("deployment:perplexity", "RESERVED", 0)
    from trade_graph.domain.errors import BudgetExhausted

    with pytest.raises(BudgetExhausted, match="allocation"):  # the wake task's EUR1.50 still bounds one round
        _reserve(route, task, max_input=10_000_000)


@pytest.mark.parametrize("case", ["not_leased", "other_role", "trader", "switched_off", "other_card",
                                  "other_deployment", "synthetic"])
def test_only_a_leased_task_of_a_switched_department_reserves_on_the_route(tmp_path, monkeypatch, case):
    account = _account(tmp_path, leader=case != "switched_off")
    route, _reads, _built = _assemble(account, monkeypatch)
    role = "leader" if case == "switched_off" else "research"
    task = _leased_task(account, "leader" if case == "other_role" else role)
    changes: dict = {"role": role}
    if case == "not_leased":
        task = Scheduler(account.database, account.clock).add_task(role="research", objective="wake:focus",
                                                                   portfolio_id=account.portfolio)
    elif case == "trader":
        changes["role"] = "trader"
    elif case == "other_card":
        changes["card"] = "subscription-no-api-card"
    elif case == "other_deployment":
        changes["deployment_id"] = "deployment"
    elif case == "synthetic":
        changes["synthetic"] = True
    with pytest.raises(PaidCallsDisabled):
        _reserve(route, task, **changes)
    assert account.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def _request(**changes) -> ModelRequest:
    values = dict(role="research", task_id="t", root_task_id="t", run_id="r", system_version_id="v",
                  provider="perplexity", model="anthropic/claude-sonnet-5-5", instructions="synthetic",
                  context={"tools": [search_tool_definition()]}, output_schema={"type": "object"},
                  schema_name="Synthetic", max_output_tokens=10, max_tool_calls=1, timeout_seconds=10)
    values.update(changes)
    return ModelRequest(**values)


@pytest.mark.parametrize("changes", [
    {"provider": "openai", "model": "gpt-6-luna"}, {"role": "trader"},
    {"context": {"tools": [dict(search_tool_definition(), name="fetch_url")]}},
    {"context": {"http_fixture": {"id": "x"}}},
])
def test_the_route_gateway_carries_only_research_and_leader_rounds_with_the_software_search(
        tmp_path, monkeypatch, changes):
    account = _account(tmp_path)
    route, _reads, _built = _assemble(account, monkeypatch)
    with pytest.raises(AuthorityDenied):
        route.gateway.invoke(_request(**changes), deployment_id=route.route.deployment_id,
                             price_card_id=AGENT_CARD_ID, fx_rate=Decimal("1"), fx_buffer=Decimal("1.02"))
    with pytest.raises(AuthorityDenied):
        route.gateway.invoke(_request(), deployment_id=route.route.deployment_id, price_card_id=AGENT_CARD_ID,
                             fx_rate=Decimal("1"), fx_buffer=Decimal("1.02"),
                             tool_handlers={"web_search": lambda _a: {}, "fetch_url": lambda _a: {}})
    assert account.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


# --- the departments ------------------------------------------------------------------------------------------------

class _Subscription:
    def __init__(self):
        self.calls = []

    def context(self, task):
        return {"input": task["input"], "model_route": {"billing_kind": "subscription"}}

    def recover(self, task):
        return None

    def __call__(self, task):
        self.calls.append(task)
        return {"summary": "subscription answer"}


def _runtime(account):
    from types import SimpleNamespace

    return SimpleNamespace(database=account.database, clock=account.clock, execution=account.execution,
                           ledger=account.ledger, portfolio_id=account.portfolio, deployment_id="deployment")


def test_with_the_switch_off_or_the_engine_off_nothing_changes_and_nothing_is_read(tmp_path, monkeypatch):
    account = _account(tmp_path, research=False, leader=False)
    reads = trust_owner_files(monkeypatch, owner_files())
    handlers = {"research": _Subscription(), "leader": _Subscription(), "trader": _Subscription()}
    for engine in (None, object()):
        result = installed.perplexity_departments(_runtime(account), OWNER, handlers, engine=engine)
        assert result == handlers and all(result[role] is handlers[role] for role in handlers)
    switch(account, research=True, leader=True)
    assert installed.perplexity_departments(_runtime(account), OWNER, handlers, engine=None) == handlers
    assert installed.perplexity_departments(_runtime(account), OWNER, {}, engine=object()) == {}
    assert reads == []  # no owner file was read, no card seeded, no transport built
    assert account.database.execute("SELECT COUNT(*) FROM price_cards").fetchone()[0] == 0


def test_switched_departments_take_their_engine_wakes_and_keep_everything_else_on_the_subscription(
        tmp_path, monkeypatch):
    account = _account(tmp_path, research=True, leader=False)
    trust_owner_files(monkeypatch, owner_files())
    monkeypatch.setattr(installed, "pinned_transport", lambda _profile: ScriptedPerplexity(account.database))
    handlers = {"research": _Subscription(), "leader": _Subscription(), "trader": _Subscription()}
    result = installed.perplexity_departments(_runtime(account), OWNER, handlers, engine=object())
    assert result["leader"] is handlers["leader"] and result["trader"] is handlers["trader"]
    research = result["research"]
    assert isinstance(research, installed.SwitchedDepartment)
    plain = {"task_id": "t-1", "input": {"objective": "research-review"}}
    assert research.context(plain) == handlers["research"].context(plain)  # the subscription snapshot, unchanged
    plain["snapshot"] = research.context(plain)
    assert research(plain) == {"summary": "subscription answer"}
    [event] = account.database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = 'perplexity_route'").fetchall()
    assert json.loads(event[0])["status"] == "ready" and json.loads(event[0])["departments"] == ["research"]


def test_a_switched_department_whose_route_is_not_ready_fails_its_wake_visibly_and_sends_nothing(
        tmp_path, monkeypatch):
    account = _account(tmp_path)
    trust_owner_files(monkeypatch, owner_files(key=None))
    handlers = {"research": _Subscription(), "leader": _Subscription()}
    result = installed.perplexity_departments(_runtime(account), OWNER, handlers, engine=object())
    wake = {"task_id": "t-2", "input": {"wake": {"purpose": "focus"}}}
    wake["snapshot"] = result["research"].context(wake)
    output = result["research"](wake)
    assert output["_status"] == "FAILED" and output["failure_scope"] == "turn"
    assert "not ready: no Perplexity key" in output["reason"] and "Nothing was sent" in output["reason"]
    assert handlers["research"].calls == []
    assert account.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_an_unreadable_switch_counts_as_off(tmp_path, monkeypatch):
    account = _account(tmp_path)
    trust_owner_files(monkeypatch, owner_files())
    monkeypatch.setattr(installed, "pinned_transport", lambda _profile: ScriptedPerplexity(account.database))
    handlers = {"research": _Subscription(), "leader": _Subscription()}
    result = installed.perplexity_departments(_runtime(account), OWNER, handlers, engine=object())
    result["research"].enabled = lambda: (_ for _ in ()).throw(sqlite3.DatabaseError("synthetic"))
    wake = {"task_id": "t-3", "input": {"wake": {"purpose": "focus"}}}
    wake["snapshot"] = result["research"].context(wake)
    assert result["research"](wake) == {"summary": "subscription answer"}


def test_the_owners_monthly_limit_pauses_only_the_round_and_nothing_is_sent(tmp_path, monkeypatch):
    """D138 item 4: once the owner records a monthly limit, reaching it pauses Perplexity rounds only."""
    from trade_graph.application.web_research import PerplexityWebResearch, WebResearchAsk
    from trade_graph.contracts.web_research import PerplexitySpendPolicy

    account = _account(tmp_path)
    transport = ScriptedPerplexity(account.database)
    route, _reads, _built = _assemble(account, monkeypatch, transport=transport)
    task = _leased_task(account)
    policy = PerplexitySpendPolicy(revision_id="synthetic-limit/spend", mode="monthly_limit",
                                   monthly_limit_usd=Decimal("0.0001"), decision_ref="D138-4")
    outcome = PerplexityWebResearch(route.gateway, route=route.route, policy=policy).run(WebResearchAsk(
        role="research", portfolio_id=account.portfolio, task_id=task, root_task_id=task, system_version_id="v",
        question="Synthetic: anything new on BTC?", max_searches=1, max_input_tokens=4000, max_output_tokens=500))
    assert outcome.status == "paused_at_limit" and "owner Perplexity limit" in outcome.reason
    assert transport.calls == [] and outcome.billing == "none"
    assert account.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert account.execution.pause(account.portfolio) is None  # no system pause: the Trader and protection go on
