"""Perplexity-backed research rounds with stored search evidence and receipts (T36).

Every Perplexity response here is a synthetic fixture played by a scripted transport, and
the key is a made-up string. No network call, no real key and no paid call: these tests
prove the software path, not Perplexity's service (that is the opt-in smoke at step L2).
"""

import copy
import itertools
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.adapters.models.perplexity import AGENT_ENDPOINT
from trade_graph.adapters.models.transport import ProviderHttpResponseError, provider_payload
from trade_graph.adapters.persistence.db import Database
from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.research import ResearchStore
from trade_graph.application.web_research import (
    AGENT_CARD_ID,
    SEARCH_CARD_ID,
    PerplexityRoute,
    PerplexitySpendGuard,
    PerplexityWebResearch,
    WebResearchAsk,
    load_price_cards,
)
from trade_graph.contracts.models import ModelRequest, PriceCard
from trade_graph.contracts.web_research import PerplexitySpendPolicy
from trade_graph.domain.clock import FrozenClock

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "perplexity"
CARDS = ROOT / "config" / "perplexity-price-cards.json"
KEY = "synthetic-test-key-not-a-real-key-0001"
UNLIMITED = PerplexitySpendPolicy(revision_id="owner-policy-test", mode="unlimited_testing", decision_ref="D125")


_IDS = itertools.count(1)


def _fixture(name: str) -> dict:
    """Each use gets its own request id, as real Perplexity responses do (receipts key on it)."""
    payload = provider_payload((FIXTURES / name).read_bytes())
    payload["id"] = f"{payload['id']}-{next(_IDS)}"
    return payload


def _refs_in(body: dict) -> list[str]:
    refs = []
    for item in body["input"]:
        if item.get("type") == "function_call_output":
            refs += [hit["source_ref"] for hit in json.loads(item["output"]).get("results", [])]
    return refs


def _final_citing(pick):
    """A final agent turn that cites whatever ``pick`` chooses from the refs the model was given."""
    def respond(body: dict) -> dict:
        payload = _fixture("agent_final.json")
        text = payload["output"][0]["content"][0]["text"]
        payload["output"][0]["content"][0]["text"] = text.replace("SOURCE_REF", pick(_refs_in(body)))
        return payload
    return respond


def _final_without_findings(body: dict) -> dict:
    payload = _fixture("agent_missing_usage.json")
    payload["usage"] = {"input_tokens": 1500, "output_tokens": 50}
    return payload


class ScriptedPerplexity:
    """Plays fixtures in order and records what the database held when each request left."""

    def __init__(self, database: Database, responses: list) -> None:
        self.database, self.responses, self.calls = database, list(responses), []

    def post_json(self, url, body, headers, *, timeout_seconds=None):
        stored = self.database.execute("SELECT COUNT(*) FROM web_search_sources").fetchone()[0]
        self.calls.append({"url": url, "body": copy.deepcopy(body), "headers": dict(headers),
                           "stored_sources": stored})
        if not self.responses:
            raise AssertionError("unexpected provider request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response(body) if callable(response) else copy.deepcopy(response)


class Setup:
    def __init__(self, tmp_path, responses, *, policy=UNLIMITED, key=KEY, paid=True):
        self.clock = FrozenClock(datetime(2026, 10, 16, 12, tzinfo=UTC))
        self.database = Database(tmp_path / "state.sqlite")
        self.budget = BudgetGateway(self.database, self.clock)
        for card in load_price_cards(CARDS):
            self.budget.seed_card(card)
        self.budget.seed_card(PriceCard(
            price_card_id="scripted", provider="scripted", model="scripted", endpoint="scripted", currency="EUR",
            input_per_million="0", output_per_million="0", effective_at="2026-10-16", verified_at="2026-10-16",
            source_id="synthetic", tier="standard", context_band="none"))
        self.budget.configure(deployment_id="deployment", currency="EUR", total=Decimal("50"), period=Decimal("50"),
                              priority_reserve=Decimal("0"), daily=Decimal("50"), root=Decimal("50"), roles={})
        self.transport = ScriptedPerplexity(self.database, responses)
        self.gateway = ModelGateway(self.budget, paid_calls_enabled=paid, transport=self.transport,
                                    api_keys={"perplexity": key} if key else {})
        self.route = PerplexityRoute(deployment_id="deployment", fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"))
        self.service = PerplexityWebResearch(self.gateway, route=self.route, policy=policy)

    def ask(self, **updates) -> WebResearchAsk:
        values = {"role": "research", "portfolio_id": "paper", "task_id": "task-1", "root_task_id": "task-1",
                  "system_version_id": "v-test", "question": "Did Solana have an outage this week?",
                  "instruments": ("SOL/USD",), "max_searches": 2, "max_input_tokens": 4000,
                  "max_output_tokens": 1000}
        values.update(updates)
        return WebResearchAsk(**values)

    def rows(self, sql: str, *params) -> list[sqlite3.Row]:
        return self.database.execute(sql, params).fetchall()

    def receipts(self) -> list[sqlite3.Row]:
        return self.rows("""SELECT r.*, b.price_card_id, b.state FROM usage_receipts r
            JOIN budget_reservations b ON b.reservation_id = r.reservation_id ORDER BY r.created_at, r.rowid""")


def _round_trip():
    return [_fixture("agent_tool_call.json"), _fixture("search_results.json"), _final_citing(lambda refs: refs[0])]


def test_round_stores_every_result_before_the_model_reads_it_and_receipts_every_call(tmp_path) -> None:
    setup = Setup(tmp_path, _round_trip())
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "completed" and outcome.billing == "resolved", outcome
    assert [call["url"] for call in setup.transport.calls] == [AGENT_ENDPOINT, SEARCH_ENDPOINT, AGENT_ENDPOINT]
    # The search's three results were stored before the request that shows them to the model.
    assert [call["stored_sources"] for call in setup.transport.calls] == [0, 0, 3]
    assert setup.transport.calls[1]["body"] == {"query": "Solana network outage October 2026", "max_results": 5,
                                                "search_recency_filter": "week"}
    replay = setup.transport.calls[2]["body"]["input"]
    assert [item["type"] for item in replay] == ["message", "function_call", "function_call_output"]
    assert json.loads(replay[1]["arguments"]) == {"query": "Solana network outage October 2026", "recency": "week"}
    shown = json.loads(replay[2]["output"])
    assert shown["status"] == "results" and len(shown["results"]) == 3
    assert "untrusted data" in shown["note"]
    assert outcome.source_refs == [hit["source_ref"] for hit in shown["results"]]

    receipts = setup.receipts()
    assert [(row["provider"], row["price_card_id"], row["status"], row["synthetic"]) for row in receipts] == [
        ("perplexity", AGENT_CARD_ID, "committed", 0),
        ("perplexity", SEARCH_CARD_ID, "committed", 0),
        ("perplexity", AGENT_CARD_ID, "committed", 0)]
    # Priced from the dated card, which matches the totals the synthetic responses report.
    assert [Decimal(row["native_cost"]) for row in receipts] == [Decimal("0.0028"), Decimal("0.005"),
                                                                 Decimal("0.008")]
    assert {row["native_currency"] for row in receipts} == {"USD"}
    steps = setup.rows("SELECT * FROM web_research_steps ORDER BY sequence")
    assert [(row["outcome"], row["reported_total_cost_usd"]) for row in steps] == [("tool_calls", "0.0028"),
                                                                                   ("output", "0.008")]
    assert [row["reservation_id"] for row in steps] == outcome.agent_reservations

    [finding_id] = outcome.finding_ids
    finding = ResearchStore(setup.database, setup.clock).require_fresh("paper", finding_id, as_of=setup.clock.now())
    # Provenance comes from the stored result, never from the model.
    assert finding.source_url == "https://news.example.org/solana-slowdown"
    assert finding.publisher == "news.example.org"
    assert finding.published_at_utc == datetime(2026, 10, 14, tzinfo=UTC)
    assert finding.relevance == "web-search" and finding.instruments == ["SOL/USD"]
    assert finding.evidence_refs[0] == outcome.source_refs[0]
    stored = setup.rows("SELECT * FROM web_search_sources WHERE source_ref = ?", outcome.source_refs[0])[0]
    assert finding.source_hash == stored["content_sha256"]
    assert setup.rows("SELECT status FROM web_research_outcomes")[0]["status"] == "completed"


def test_the_key_is_only_ever_a_request_header(tmp_path) -> None:
    setup = Setup(tmp_path, _round_trip())
    outcome = setup.service.run(setup.ask())
    for call in setup.transport.calls:
        assert call["headers"] == {"Authorization": f"Bearer {KEY}"}
        assert KEY not in json.dumps(call["body"])
    assert KEY not in outcome.model_dump_json()
    setup.database.close()
    dump = "\n".join(sqlite3.connect(tmp_path / "state.sqlite").iterdump())
    assert KEY not in dump and "Bearer" not in dump


def test_a_finding_citing_a_source_outside_its_snapshot_is_rejected(tmp_path) -> None:
    responses = _round_trip()
    responses[-1] = _final_citing(lambda refs: "web:never-stored:1")
    setup = Setup(tmp_path, responses)
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "rejected" and "outside the persisted snapshot" in outcome.reason
    assert outcome.finding_ids == [] and setup.rows("SELECT * FROM findings") == []
    # The evidence and the bill stay: three receipts and three stored results.
    assert len(setup.receipts()) == 3 and len(setup.rows("SELECT * FROM web_search_sources")) == 3


def test_a_source_stored_for_another_round_is_outside_this_snapshot(tmp_path) -> None:
    first = _round_trip()
    setup = Setup(tmp_path, first)
    earlier = setup.service.run(setup.ask())
    assert earlier.status == "completed"
    setup.transport.responses = [_final_citing(lambda refs: earlier.source_refs[0])]
    later = setup.service.run(setup.ask(task_id="task-2", root_task_id="task-2"))
    assert later.status == "rejected" and later.finding_ids == []
    assert len(setup.rows("SELECT * FROM findings")) == 1


def test_a_refusal_is_a_failed_round_with_a_committed_receipt(tmp_path) -> None:
    setup = Setup(tmp_path, [_fixture("agent_refusal.json")])
    outcome = setup.service.run(setup.ask())
    assert (outcome.status, outcome.failure, outcome.billing) == ("failed", "refusal", "resolved")
    [receipt] = setup.receipts()
    assert Decimal(receipt["native_cost"]) == (900 * Decimal(2) + 12 * Decimal(10)) / Decimal(1_000_000)
    assert tuple(setup.rows("SELECT outcome, failure FROM web_research_steps")[0]) == ("failed", "refusal")


def test_a_timeout_leaves_the_bill_unresolved_never_free(tmp_path) -> None:
    setup = Setup(tmp_path, [TimeoutError("synthetic timeout")])
    outcome = setup.service.run(setup.ask())
    assert (outcome.status, outcome.billing) == ("uncertain", "unresolved")
    assert outcome.unresolved_reservations == outcome.agent_reservations
    assert setup.receipts() == []
    [reservation] = setup.rows("SELECT state, amount FROM budget_reservations")
    assert reservation["state"] == "UNCERTAIN" and Decimal(reservation["amount"]) > 0


def test_missing_usage_keeps_the_reservation_held_and_flags_the_round(tmp_path) -> None:
    setup = Setup(tmp_path, [_fixture("agent_missing_usage.json")])
    outcome = setup.service.run(setup.ask())
    assert (outcome.status, outcome.billing) == ("completed", "unresolved")
    assert outcome.reply == {"summary": "Nothing relevant found.", "findings": []}
    assert setup.receipts() == []
    assert [row["state"] for row in setup.rows("SELECT state FROM budget_reservations")] == ["UNCERTAIN"]


def test_malformed_search_results_are_billed_but_never_stored_or_citable(tmp_path) -> None:
    setup = Setup(tmp_path, [_fixture("agent_tool_call.json"), _fixture("search_malformed.json"),
                             _final_without_findings])
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "completed" and outcome.source_refs == []
    shown = json.loads(setup.transport.calls[2]["body"]["input"][2]["output"])
    assert shown == {"status": "malformed", "results": [], "note": shown["note"]}
    [search] = setup.rows("SELECT * FROM web_search_outcomes")
    assert (search["outcome"], search["result_count"]) == ("malformed", 0)
    assert search["receipt_id"] is not None  # the request was answered, so it was billed
    assert setup.rows("SELECT * FROM web_search_sources") == []


@pytest.mark.parametrize(("error", "kind"), [(TimeoutError("synthetic"), "uncertain"),
                                             (ProviderHttpResponseError(401, {}), "failed"),
                                             (ProviderHttpResponseError(503, {}), "failed")])
def test_a_failed_search_keeps_its_reservation_unresolved(tmp_path, error, kind) -> None:
    setup = Setup(tmp_path, [_fixture("agent_tool_call.json"), error, _final_without_findings])
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "completed" and outcome.billing == "unresolved"
    [search] = setup.rows("SELECT * FROM web_search_outcomes")
    assert search["outcome"] == kind and search["receipt_id"] is None
    [call] = setup.rows("SELECT reservation_id FROM web_search_calls")
    assert call["reservation_id"] in outcome.unresolved_reservations
    shown = json.loads(setup.transport.calls[2]["body"]["input"][2]["output"])
    assert shown["status"] == "failed" and shown["results"] == []


def test_invalid_search_arguments_are_refused_without_a_paid_search(tmp_path) -> None:
    turn = _fixture("agent_tool_call.json")
    turn["output"][0]["arguments"] = json.dumps({"query": "", "recency": None})
    setup = Setup(tmp_path, [turn, _final_without_findings])
    outcome = setup.service.run(setup.ask())
    # The gateway refuses arguments outside the registered tool schema before any search runs.
    assert outcome.status == "failed" and outcome.failure == "validation"
    assert setup.rows("SELECT * FROM web_search_calls") == []
    assert [call["url"] for call in setup.transport.calls] == [AGENT_ENDPOINT]


def test_without_an_owner_policy_nothing_is_reserved_or_sent(tmp_path) -> None:
    setup = Setup(tmp_path, [], policy=None)
    outcome = setup.service.run(setup.ask())
    assert (outcome.status, outcome.billing) == ("disabled", "none")
    assert "owner" in outcome.reason
    assert setup.rows("SELECT * FROM budget_reservations") == [] and setup.transport.calls == []
    assert setup.rows("SELECT status FROM web_research_outcomes")[0]["status"] == "disabled"


def test_paid_calls_off_means_no_perplexity_call(tmp_path) -> None:
    setup = Setup(tmp_path, [], paid=False)
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "disabled" and setup.transport.calls == []
    assert setup.rows("SELECT * FROM budget_reservations") == []


def _limited(amount: str) -> PerplexitySpendPolicy:
    return PerplexitySpendPolicy(revision_id="owner-limit-test", mode="monthly_limit", monthly_limit_usd=amount,
                                 decision_ref="owner-revision-test")


def test_an_owner_limit_pauses_rounds_while_trader_and_records_continue(tmp_path) -> None:
    # Each agent step reserves at most (4000 x 2 + 1000 x 10) / 1e6 + 2 x 0.0025 = 0.023 USD, x 1.02 = 0.02346.
    setup = Setup(tmp_path, _round_trip(), policy=_limited("0.03"))
    outcome = setup.service.run(setup.ask())
    # Step 1 (0.0028) and the search (0.005) fit; step 2 would pass 0.03 at its reserved maximum.
    assert outcome.status == "paused_at_limit" and "limit" in outcome.reason
    assert [call["url"] for call in setup.transport.calls] == [AGENT_ENDPOINT, SEARCH_ENDPOINT]
    assert len(setup.receipts()) == 2 and len(setup.rows("SELECT * FROM budget_reservations")) == 2
    assert len(setup.rows("SELECT * FROM web_search_sources")) == 3  # evidence already stored is kept
    guard = PerplexitySpendGuard(setup.database, setup.clock, _limited("0.03"), deployment_id="deployment")
    assert guard.spent_usd() == Decimal("0.0078")

    again = setup.service.run(setup.ask(task_id="task-2", root_task_id="task-2"))
    assert again.status == "paused_at_limit" and len(setup.transport.calls) == 2
    assert len(setup.rows("SELECT * FROM budget_reservations")) == 2

    # The Trader's model path and every record keep working; nothing paused the system.
    trader = setup.gateway.invoke(
        ModelRequest(role="trader", task_id="trade-1", root_task_id="trade-1", run_id="r", system_version_id="v",
                     provider="scripted", model="scripted", instructions="decide",
                     context={"scripted_result": {"action": "hold"}}, output_schema={"type": "object"},
                     schema_name="decision", max_output_tokens=10, max_tool_calls=0, timeout_seconds=5,
                     synthetic=True),
        deployment_id="deployment", price_card_id="scripted", fx_rate=Decimal("1"), fx_buffer=Decimal("1"))
    assert trader.ok and trader.payload == {"action": "hold"}
    assert setup.rows("SELECT * FROM pause_states") == []
    assert [row["status"] for row in setup.rows("SELECT status FROM web_research_outcomes")] == [
        "paused_at_limit", "paused_at_limit"]


def test_unlimited_testing_mode_never_pauses_but_still_receipts_every_call(tmp_path) -> None:
    setup = Setup(tmp_path, _round_trip() + _round_trip())
    assert setup.service.run(setup.ask()).status == "completed"
    assert setup.service.run(setup.ask(task_id="task-2", root_task_id="task-2")).status == "completed"
    assert len(setup.receipts()) == 6
    guard = PerplexitySpendGuard(setup.database, setup.clock, UNLIMITED, deployment_id="deployment")
    assert guard.spent_usd() == 2 * Decimal("0.0158")


def test_unresolved_calls_count_at_their_reserved_maximum(tmp_path) -> None:
    setup = Setup(tmp_path, [TimeoutError("synthetic")], policy=_limited("0.04"))
    assert setup.service.run(setup.ask()).status == "uncertain"
    guard = PerplexitySpendGuard(setup.database, setup.clock, _limited("0.04"), deployment_id="deployment")
    assert guard.spent_usd() == Decimal("0.02346")
    # A second step at 0.02346 would pass 0.04, so the next round pauses before sending anything.
    assert setup.service.run(setup.ask(task_id="task-2", root_task_id="task-2")).status == "paused_at_limit"
    assert len(setup.transport.calls) == 1


def test_a_retry_is_a_new_round_with_its_own_reservation(tmp_path) -> None:
    rate_limited = {"id": "resp_rate", "error": {"type": "rate_limit_exceeded"},
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
    setup = Setup(tmp_path, [rate_limited, _final_without_findings])
    first = setup.service.run(setup.ask())
    assert (first.status, first.failure) == ("failed", "rate_limit")
    second = setup.service.run(setup.ask(task_id="task-1-retry"))
    assert second.status == "completed"
    assert set(first.agent_reservations).isdisjoint(second.agent_reservations)
    assert [row["status"] for row in setup.receipts()] == ["committed", "committed"]


def test_synthetic_label_follows_the_missing_key(tmp_path) -> None:
    setup = Setup(tmp_path, _round_trip(), key=None)
    outcome = setup.service.run(setup.ask())
    assert outcome.status == "completed"
    assert {row["synthetic"] for row in setup.receipts()} == {1}
    assert {row["headers"].get("Authorization") for row in setup.transport.calls} == {None}


def test_policy_contract_has_no_default_and_testing_mode_is_d125() -> None:
    with pytest.raises(ValueError, match="D125"):
        PerplexitySpendPolicy(revision_id="r", mode="unlimited_testing", decision_ref="D999")
    with pytest.raises(ValueError, match="no limit"):
        PerplexitySpendPolicy(revision_id="r", mode="unlimited_testing", decision_ref="D125", monthly_limit_usd="5")
    for amount in (None, "0", "-1", "NaN"):
        with pytest.raises(ValueError):
            PerplexitySpendPolicy(revision_id="r", mode="monthly_limit", decision_ref="x", monthly_limit_usd=amount)
    assert _limited("15.50").model_dump(mode="json")["monthly_limit_usd"] == "15.5"


def test_the_shipped_price_cards_are_the_dated_records() -> None:
    cards = {card.price_card_id: card for card in load_price_cards(CARDS)}
    agent, search = cards[AGENT_CARD_ID], cards[SEARCH_CARD_ID]
    assert (agent.provider, agent.model, agent.endpoint, agent.currency) == (
        "perplexity", "anthropic/claude-sonnet-5-5", AGENT_ENDPOINT, "USD")
    assert (agent.input_per_million, agent.output_per_million, agent.search_per_call) == (
        Decimal("2"), Decimal("10"), Decimal("0.0025"))
    assert (agent.effective_at, agent.verified_at) == ("2026-10-12", "2026-10-12")
    assert (search.provider, search.endpoint, search.search_per_call) == ("perplexity", SEARCH_ENDPOINT,
                                                                         Decimal("0.005"))
    assert search.input_per_million == search.output_per_million == 0
    prices = json.loads((ROOT / "planning" / "model-prices.json").read_text())
    planned = prices["models"]["perplexity:anthropic/claude-sonnet-5-5"]
    assert (Decimal(planned["input_per_million"]), Decimal(planned["output_per_million"]),
            Decimal(planned["web_search_per_call"]), Decimal(planned["fetch_url_per_call"])) == (
        Decimal("2"), Decimal("10"), Decimal("0.0025"), Decimal("0.0005"))
    assert Decimal(prices["models"]["perplexity:search-api"]["request_per_call"]) == Decimal("0.005")
    # Older entries are untouched (they are dated planning records).
    assert prices["verified_at"] == "2026-09-29"
    assert prices["models"]["anthropic:claude-sonnet-5-5"] == {"input_per_million": "2.00",
                                                               "output_per_million": "10.00", "source_id": "S02"}
