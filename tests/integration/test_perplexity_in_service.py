"""A scripted day in the installed subscription service with Research and the Leader on Perplexity (W8-A acceptance).

``run_subscription_operation`` runs the real protected subscription binding (synthetic CLI, no model call) with the
event engine on. With the owner's switch on, the day start's Research focus step and the Leader's news round run on
Perplexity in the protected process through the reviewed route, with receipts and stored sources; the Trader stays on
its subscription. With the switch off the same day runs exactly as before: both departments answer on the
subscription and nothing Perplexity-related is read, seeded or sent.

Everything is synthetic: the account, a recorded live scan, the owner's folder (``tests/perplexity_service_support``),
price cards re-dated for the test clock and labelled synthetic, Perplexity answers in the documented shapes played by
a scripted transport with a made-up key. No network, real key, paid call or order. A scripted run is never a
paid-provider test.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from tests.focus_research_support import BTC_NEWS, final, finding, focus_answer, prompt, record_scan, ref_for
from tests.focus_research_support import search as research_search
from tests.focus_research_support import tool_call as research_tool_call
from tests.integration.test_owned_subscription_service import subscription_service
from tests.integration.test_subscription_trader_wakes import WAKE_GRAPH
from tests.leader_news_support import full_round
from tests.leadership_support import reply as leader_reply
from tests.perplexity_service_support import CARDS, KEY, owner_files, trust_owner_files

from trade_graph.adapters.models.perplexity import AGENT_ENDPOINT
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT
from trade_graph.application import perplexity_installation as installed
from trade_graph.application import subscription_operation as operation
from trade_graph.application.perplexity_switch import record_perplexity_departments
from trade_graph.application.service_market_data import open_service_market_data
from trade_graph.paper_runtime import PaperRuntimeConfig

# The dated 2026 cards, re-dated for this account's clock (2026-01-01) under new synthetic ids; prices unchanged.
SYNTHETIC_CARDS = json.loads(CARDS)
for _card in SYNTHETIC_CARDS["price_cards"]:
    _card.update(price_card_id="synthetic-" + _card["price_card_id"], effective_at="2025-12-20",
                 verified_at="2025-12-20", source_id="synthetic-test")
SYNTHETIC_CARDS = json.dumps(SYNTHETIC_CARDS).encode()


class RoutedPerplexity:
    """A scripted Perplexity that answers each department from its own script: an Agent API request names its role
    in the protected instructions; a search belongs to the round that asked for it."""

    def __init__(self, database, scripts: dict[str, list]) -> None:
        self.database, self.scripts, self.calls, self.role = database, scripts, [], None

    def post_json(self, url, body, headers, *, timeout_seconds=None):
        if url == AGENT_ENDPOINT:
            self.role = "leader" if "the leader department's" in body["instructions"] else "research"
        stored = self.database.execute("SELECT COUNT(*) FROM web_search_sources").fetchone()[0]
        self.calls.append({"role": self.role, "url": url, "headers": dict(headers), "stored_sources": stored})
        script = self.scripts[self.role]
        if not script:
            raise AssertionError(f"unexpected {self.role} request")
        response = script.pop(0)
        return response(copy.deepcopy(body)) if callable(response) else copy.deepcopy(response)


def _research_round() -> list:
    def answer(body: dict) -> dict:
        btc = ref_for(body, BTC_NEWS["url"])
        evidence = [btc, prompt(body)["scan"]["scan_id"]]
        return focus_answer([("BTC/USD", "Synthetic: quiet ahead of data, liquid.", evidence)],
                            findings=[finding(btc, "BTC/USD")], summary="BTC today.")

    def keep(body: dict) -> dict:  # the Leader's deep dive wakes Research again: a refresh that keeps today's focus
        return focus_answer([], summary="Nothing new on BTC; keep today's focus.", keep=True)

    return [research_tool_call("Bitcoin news"), research_search(BTC_NEWS), final(answer),
            research_tool_call("Kraken SOL deposit delays history"), research_search(BTC_NEWS), final(keep)]


@pytest.fixture
def day(tmp_path, monkeypatch):
    flow, runtime, binding, _service, cli, _current = subscription_service(tmp_path, monkeypatch, source=WAKE_GRAPH)
    config = PaperRuntimeConfig(tick_interval_seconds=0.01, schedule_intervals={}, public_data_enabled=True,
                                event_engine=True, decision_candle_wait_seconds=0)
    runtime.config, runtime.ledger, runtime.public_feed = config, runtime.execution.ledger, None
    runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
    runtime.market_data = open_service_market_data(tmp_path / "market" / "market_data.sqlite", database=flow.db,
                                                   clock=flow.clock, portfolio_ids=[flow.pid])
    runtime.price_collector = None
    common = {"evidence_refs": [flow.ref], "summary": "Bounded evidence.", "outcome": "Insufficient sample."}
    cli.outputs.update({"leader": leader_reply([flow.ref]), "learning": {**common, "lessons": []},
                        "optimisation": {**common, "proposals": []},
                        "trader": {"day_plans": [], "next_check_in": None,
                                   "summary": "Synthetic: no plan today."}})
    record_scan(flow.db, ["BTC/USD", "ETH/USD", "SOL/USD"], as_of="2025-12-31T23:00:00.000000Z")
    monkeypatch.setattr(operation, "assert_boot_environment", lambda: None)
    monkeypatch.setattr(operation, "read_owner_file", lambda *_a: runtime.config.model_dump_json().encode())
    monkeypatch.setattr(operation, "assemble_paper_runtime", lambda _path, **_kwargs: runtime)
    reads = trust_owner_files(monkeypatch, owner_files(cards=SYNTHETIC_CARDS))
    transport = RoutedPerplexity(flow.db, {"research": _research_round(), "leader": full_round()})
    built = []

    def pinned(profile):
        built.append(profile)
        return transport

    monkeypatch.setattr(installed, "pinned_transport", pinned)
    yield SimpleNamespace(flow=flow, runtime=runtime, cli=cli, owner=tmp_path / "owner", transport=transport,
                          built=built, reads=reads)


def _run(day, ticks=40) -> tuple[dict, Database]:
    result = operation.run_subscription_operation(day.flow.db.path, day.owner, maximum_ticks=ticks)
    return result, Database(day.flow.db.path)


def _rows(database, sql, *params):
    return [dict(row) for row in database.execute(sql, params).fetchall()]


def test_with_the_switch_on_the_day_runs_research_and_a_leader_round_on_perplexity(day) -> None:
    record_perplexity_departments(day.flow.db, day.flow.clock, research=True, leader=True, monthly_limit_usd=None,
                                  revision_id="synthetic-owner-switch-l3")
    result, database = _run(day)
    try:
        assert result["event_engine"] is True and result["paid_authorization"] is False
        assert not [item for item in result["failures"] if item.startswith("wakes")], result["failures"]
        # The route was built once, from the owner's folder, through the reviewed paid_research route.
        assert [profile.route_url("paid_research") for profile in day.built] == ["http://172.29.17.2:8082"]
        assert {"perplexity.key", "perplexity-price-cards.json"} <= set(day.reads)
        [route] = _rows(database, "SELECT payload_json FROM activity_events WHERE kind = 'perplexity_route'")
        assert json.loads(route["payload_json"])["status"] == "ready"

        # Research's focus step ran on Perplexity and named BTC/USD from the shortlist inside the coin list.
        steps = {row["step"]: row["status"] for row in _rows(database, "SELECT step, status FROM day_cycle_steps")}
        assert steps["focus"] == "done" and steps["day_plan"] == "done", steps
        [focus, refresh] = _rows(database, "SELECT r.purpose, o.status, o.focus_symbols_json, o.billing "
                                           "FROM focus_research_rounds r JOIN focus_research_outcomes o "
                                           "ON o.round_id = r.round_id ORDER BY r.sequence")
        assert (focus["purpose"], focus["status"], json.loads(focus["focus_symbols_json"]), focus["billing"]) == (
            "focus", "completed", ["BTC/USD"], "resolved")
        # One Leader news round on Perplexity; its deep dive woke Research for a refresh that kept today's focus.
        [leader] = _rows(database, "SELECT status, run_id FROM leader_news_rounds")
        assert leader["status"] == "completed"
        assert (refresh["purpose"], refresh["status"], refresh["billing"]) == ("refresh", "completed", "resolved")
        kinds = {row["kind"] for row in _rows(database, "SELECT kind FROM leader_round_requests")}
        assert {"trader_note", "deep_dive"} <= kinds

        # Every call: a reservation on the route's deployment and a committed receipt; sources stored first.
        calls = day.transport.calls
        by_role = {role: [call["url"] for call in calls if call["role"] == role] for role in ("research", "leader")}
        assert by_role == {"research": [AGENT_ENDPOINT, SEARCH_ENDPOINT, AGENT_ENDPOINT] * 2,
                           "leader": [AGENT_ENDPOINT, SEARCH_ENDPOINT] * 2 + [AGENT_ENDPOINT]}
        assert day.transport.scripts == {"research": [], "leader": []}  # every scripted answer was used
        assert {call["headers"]["Authorization"] for call in calls} == {f"Bearer {KEY}"}
        receipts = _rows(database, """SELECT r.provider, r.status, r.synthetic, b.deployment_id, b.role, b.state
            FROM usage_receipts r JOIN budget_reservations b ON b.reservation_id = r.reservation_id
            WHERE r.provider = 'perplexity'""")
        assert len(receipts) == len(calls) == 11
        assert {(row["status"], row["synthetic"], row["deployment_id"], row["state"]) for row in receipts} == {
            ("committed", 0, "deployment:perplexity", "COMMITTED")}
        assert {row["role"] for row in receipts} == {"research", "leader"}
        assert len(_rows(database, "SELECT * FROM web_search_sources")) == 1 + 5 + 1
        finals = [call for call in calls if call["url"] == AGENT_ENDPOINT]
        assert finals[-1]["stored_sources"] > 0  # results were stored before the model read them

        # The Trader stayed on its subscription and was woken; Research and the Leader sent nothing to it.
        assert "trader" in [request.role for request in day.cli.requests]
        assert all("wake" not in request.context for request in day.cli.requests
                   if request.role in {"research", "leader"})
        assert database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
        dump = "\n".join(database.connection.iterdump())
        assert KEY not in dump and "Bearer" not in dump
    finally:
        database.close()


def test_with_the_switch_off_the_same_day_runs_as_before(day) -> None:
    cards_before = day.flow.db.execute("SELECT COUNT(*) FROM price_cards").fetchone()[0]
    result, database = _run(day)
    try:
        assert result["event_engine"] is True
        assert not [item for item in result["failures"] if item.startswith("wakes")], result["failures"]
        # Nothing Perplexity-related was read, seeded, built or sent. (The news check, W9-A, reads the network profile
        # at start to see whether its own reviewed route is listed; this profile lists none, so it reads nothing else.)
        assert day.built == [] and day.transport.calls == [] and day.reads == ["subscription-network-profile.json"]
        assert database.execute("SELECT COUNT(*) FROM price_cards").fetchone()[0] == cards_before
        assert _rows(database, "SELECT * FROM activity_events WHERE kind = 'perplexity_route'") == []
        assert _rows(database, "SELECT * FROM focus_research_rounds") == []
        assert _rows(database, "SELECT * FROM leader_news_rounds") == []
        assert _rows(database, "SELECT * FROM budget_reservations WHERE deployment_id = 'deployment:perplexity'") == []
        # Research and the Leader answered on the subscription; the focus step failed visibly (no focus coins from a
        # subscription answer, D223) and the Trader was still woken for its day plans.
        roles = [request.role for request in day.cli.requests]
        assert {"research", "leader", "trader"} <= set(roles)
        steps = {row["step"]: row["status"] for row in _rows(database, "SELECT step, status FROM day_cycle_steps")}
        assert steps["focus"] == "failed" and steps["day_plan"] == "done", steps
    finally:
        database.close()


def test_switched_on_without_the_owner_key_the_wakes_fail_visibly_and_nothing_is_sent(day, monkeypatch) -> None:
    record_perplexity_departments(day.flow.db, day.flow.clock, research=True, leader=True, monthly_limit_usd=None,
                                  revision_id="synthetic-owner-switch-no-key")
    trust_owner_files(monkeypatch, owner_files(key=None, cards=SYNTHETIC_CARDS))
    result, database = _run(day)
    try:
        assert day.built == [] and day.transport.calls == []
        [route] = _rows(database, "SELECT payload_json FROM activity_events WHERE kind = 'perplexity_route'")
        assert json.loads(route["payload_json"])["status"] == "not_ready"
        steps = {row["step"]: row for row in _rows(database, "SELECT step, status, detail_json FROM day_cycle_steps")}
        assert steps["focus"]["status"] == "failed" and "no Perplexity key" in steps["focus"]["detail_json"]
        assert steps["day_plan"]["status"] == "done"  # the Trader was still woken
        assert _rows(database, "SELECT * FROM budget_reservations WHERE deployment_id = 'deployment:perplexity'") == []
        # A switched department's engine wakes never fall back silently to the subscription: they fail with the
        # reason. Leader tasks the engine did not make (the Secretary's digests) stay on the subscription (D210).
        wakes = _rows(database, "SELECT role, status, output_json FROM tasks WHERE role IN ('leader', 'research') "
                                "AND objective LIKE 'wake:%'")
        assert wakes and {row["status"] for row in wakes} == {"FAILED"}
        assert all("not ready: no Perplexity key" in json.loads(row["output_json"])["reason"] for row in wakes)
        assert all("wake" not in request.context for request in day.cli.requests
                   if request.role in {"research", "leader"})
    finally:
        database.close()


def test_the_repositorys_dated_cards_are_what_the_owner_installs_and_they_go_stale_on_2026_11_11() -> None:
    from trade_graph.contracts.models import PriceCard

    cards = [PriceCard.model_validate(item) for item in json.loads(CARDS)["price_cards"]]
    agent, search = installed.select_cards(cards, datetime(2026, 11, 10, tzinfo=UTC).date())
    assert (agent.price_card_id, search.price_card_id) == (
        "perplexity-agent-claude-sonnet-5-5-2026-10-12", "perplexity-search-api-2026-10-16")
    assert installed.card_problem(agent, datetime(2026, 11, 10, tzinfo=UTC).date()) is None
    assert "went stale on 2026-11-11" in installed.card_problem(agent, datetime(2026, 11, 11, tzinfo=UTC).date())
    assert "went stale on 2026-11-15" in installed.card_problem(search, datetime(2026, 11, 15, tzinfo=UTC).date())
