"""Helpers for Research's focus-coin tests (T37): a synthetic account, a recorded scan, the owner switch and a scripted
Perplexity transport.

Everything is synthetic and labelled so: the USD10,000 day-plan account (SOL near 100), a live coin scan written
straight into migration 0031 with made-up inputs, Agent API and Search API answers built by hand in the documented
shapes (see tests/fixtures/perplexity/README.md) and a made-up key. No network, model, real key, paid call or order:
these tests prove the software path, never Perplexity's service.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.day_plan_support import day_plan_account
from tests.wake_support import engine_for
from trade_graph.adapters.models.transport import provider_payload
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.focus_research import FocusResearch
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.perplexity_switch import record_perplexity_departments
from trade_graph.application.web_research import PerplexityRoute, load_price_cards
from trade_graph.domain.clock import utc_iso

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "perplexity"
CARDS = ROOT / "config" / "perplexity-price-cards.json"
KEY = "synthetic-test-key-not-a-real-key-0002"
_IDS = itertools.count(1)


# --- the day's scan ----------------------------------------------------------------------------------------------

def inputs(volume: str, swing: str, spread: str, trend: str) -> dict[str, Any]:
    return {"volume_usd_24h": volume, "swing_to_cost": swing, "spread": spread, "trend": trend,
            "source_time_utc": "2026-10-15T23:00:00.000000Z", "label": "synthetic"}


SHORTLIST = {"SOL/USD": inputs("41000000", "4.1", "0.0004", "0.031"),
             "BTC/USD": inputs("900000000", "2.2", "0.0001", "0.004"),
             "ADA/USD": inputs("8000000", "5.0", "0.0009", "0.052")}


def record_scan(database: Database, symbols: Mapping[str, dict[str, Any]] | Iterable[str] = SHORTLIST, *,
                as_of: str, scan_id: str = "scan-synthetic-1", mode: str = "live",
                status: str = "ok") -> str:
    """A live (or replay) scan written straight into migration 0031, labelled synthetic, with made-up inputs."""
    entries = dict(symbols) if isinstance(symbols, Mapping) else {symbol: inputs("1", "1", "0", "0")
                                                                 for symbol in symbols}
    document = json.dumps({"label": "synthetic focus-research test scan", "excluded": {}}, sort_keys=True,
                          separators=(",", ":"))
    count = len(entries) if status == "ok" else 0
    with database.transaction() as connection:
        sequence = connection.execute(
            """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status,
               error, evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
               VALUES (?, ?, ?, ?, ?, 'coin-scanner-r1-synthetic/000000000000', ?, ?, 'synthetic_fixture', ?, ?, ?, ?,
               ?, ?)""",
            (scan_id, mode, as_of, as_of, as_of, status, "synthetic_failure" if status == "failed" else None,
             int(mode == "replay"), max(count, 1), count, count, document,
             hashlib.sha256(document.encode("ascii")).hexdigest())).lastrowid
        if status == "ok":
            for position, (symbol, values) in enumerate(entries.items(), start=1):
                connection.execute("INSERT INTO coin_scan_pairs VALUES (?, ?, ?, ?, 1, '{}')",
                                   (sequence, position, symbol, position))
                entry = {"position": position, "symbol": symbol, "score": position, "ranks": {},
                         "ranking_version": "coin-scanner-r1-synthetic/000000000000",
                         "flags": ["not_individually_reviewed"] if symbol == "ADA/USD" else [], "inputs": values}
                connection.execute("INSERT INTO coin_shortlist_entries VALUES (?, ?, ?, ?)",
                                   (sequence, position, symbol, json.dumps(entry, sort_keys=True)))
    return scan_id


# --- scripted Perplexity -----------------------------------------------------------------------------------------

def _payload(name: str) -> dict[str, Any]:
    payload = provider_payload((FIXTURES / name).read_bytes())
    payload["id"] = f"{payload['id']}-focus-{next(_IDS)}"  # each answer its own request id, as real ones have
    return payload


def tool_call(*queries: str, recency: str | None = "week") -> Callable[[dict], dict]:
    """An Agent API turn asking the software-run web_search tool, once per query."""

    def respond(_body: dict) -> dict:
        payload = _payload("agent_tool_call.json")
        template = payload["output"][0]
        payload["output"] = [{**template, "id": f"fc_{index}", "call_id": f"call_focus_{next(_IDS)}",
                              "arguments": json.dumps({"query": query, "recency": recency})}
                             for index, query in enumerate(queries)]
        return payload

    return respond


def search(*results: dict[str, Any]) -> Callable[[dict], dict]:
    """A Search API answer with the given results (title, url, snippet, date)."""

    def respond(_body: dict) -> dict:
        return {"id": f"search_focus_{next(_IDS)}", "results": [dict(result) for result in results]}

    return respond


def fixture(name: str) -> Callable[[dict], dict]:
    return lambda _body: _payload(name) if name.startswith("agent") else provider_payload(
        (FIXTURES / name).read_bytes())


def result(title: str, url: str, snippet: str, date: str | None = "2026-10-15") -> dict[str, Any]:
    return {"title": title, "url": url, "snippet": snippet, "date": date}


SOL_NEWS = result("Solana upgrade ships this week", "https://news.example.org/solana-upgrade",
                  "Validators adopted the synthetic upgrade on Thursday; fees fell.")
BTC_NEWS = result("Bitcoin quiet ahead of data", "https://markets.example.com/btc-quiet",
                  "Synthetic: traders wait for Friday's figures; the price barely moved.")


def shown(body: dict) -> list[dict[str, Any]]:
    """Every search result the model has been shown so far in this round, as the request replays them."""
    hits: list[dict[str, Any]] = []
    for item in body["input"]:
        if item.get("type") == "function_call_output":
            hits += json.loads(item["output"]).get("results", [])
    return hits


def prompt(body: dict) -> dict[str, Any]:
    """The JSON context the model reads (the first input message)."""
    return json.loads(body["input"][0]["content"])


def final(answer: dict[str, Any] | Callable[[dict], dict[str, Any]]) -> Callable[[dict], dict]:
    """An Agent API turn whose structured output is ``answer`` (a callable sees the request and builds it)."""

    def respond(body: dict) -> dict:
        payload = _payload("agent_final.json")
        document = answer(body) if callable(answer) else answer
        payload["output"][0]["content"][0]["text"] = json.dumps(document)
        return payload

    return respond


def ref_for(body: dict, url: str) -> str:
    return next(hit["source_ref"] for hit in shown(body) if hit["url"] == url)


def finding(ref: str, symbol: str, claim: str = "A synthetic outlet reports news.") -> dict[str, Any]:
    return {"question": f"Any news on {symbol} today?", "claim": claim, "source_ref": ref, "instruments": [symbol],
            "counterevidence": "One synthetic source only.", "invalidation": "A later report contradicts it."}


def focus_answer(picks: list[tuple[str, str, list[str]]], *, findings: list[dict] | None = None,
                 summary: str = "Synthetic focus.", check_in: str | None = None, keep: bool = False,
                 **extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "summary": summary, "findings": findings or [], "keep_current_focus": keep,
        "focus": [{"symbol": symbol, "reason": reason, "evidence_refs": refs} for symbol, reason, refs in picks],
        "next_check_in": None if check_in is None else {"at_utc": check_in, "note": "synthetic next look"}}
    document.update(extra)
    return document


class ScriptedPerplexity:
    """Plays answers in order and records each request and how many results were stored when it left."""

    def __init__(self, database: Database, responses: list | None = None) -> None:
        self.database, self.responses, self.calls = database, list(responses or []), []

    def add(self, *responses) -> None:
        self.responses.extend(responses)

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


@dataclass
class Research:
    account: Any
    scheduler: Any
    engine: Any
    transport: ScriptedPerplexity
    gateway: ModelGateway
    route: PerplexityRoute
    handler: FocusResearch

    def receipts(self) -> list[dict[str, Any]]:
        rows = self.account.database.execute(
            """SELECT r.*, b.price_card_id, b.role, b.task_id FROM usage_receipts r
               JOIN budget_reservations b ON b.reservation_id = r.reservation_id ORDER BY r.created_at, r.rowid""")
        return [dict(row) for row in rows]

    def rounds(self) -> list[dict[str, Any]]:
        rows = self.account.database.execute(
            """SELECT r.*, o.status, o.answered, o.reason, o.web_run_id, o.billing, o.focus_symbols_json,
               o.output_json FROM focus_research_rounds r LEFT JOIN focus_research_outcomes o
               ON o.round_id = r.round_id ORDER BY r.sequence""")
        return [dict(row) for row in rows]


def perplexity(database: Database, clock, responses: list | None = None, *, key: str | None = KEY,
               paid: bool = True) -> tuple[ScriptedPerplexity, ModelGateway, PerplexityRoute]:
    budget = BudgetGateway(database, clock)
    for card in load_price_cards(CARDS):
        budget.seed_card(card)
    budget.configure(deployment_id="deployment", currency="EUR", total=Decimal("50"), period=Decimal("50"),
                     priority_reserve=Decimal("0"), daily=Decimal("50"), root=Decimal("50"), roles={})
    transport = ScriptedPerplexity(database, responses)
    gateway = ModelGateway(budget, paid_calls_enabled=paid, transport=transport,
                           api_keys={"perplexity": key} if key else {})
    return transport, gateway, PerplexityRoute(deployment_id="deployment", fx_rate=Decimal("0.9"))


def switch(account, *, research: bool = True, leader: bool = False, limit: Decimal | None = None,
           revision: str | None = None) -> None:
    record_perplexity_departments(account.database, account.clock, research=research, leader=leader,
                                  monthly_limit_usd=limit, revision_id=revision or f"perplexity-{next(_IDS)}")


def research_setup(tmp_path, responses: list | None = None, *, departments=("trader", "research"),
                   scan: Mapping[str, dict[str, Any]] | Iterable[str] | None = SHORTLIST, switched_on: bool = True,
                   now=None, key: str | None = KEY, paid: bool = True, **engine_options: Any) -> Research:
    """The day-plan account at 00:00:30 UTC with a live scan recorded at 23:00, the switch, and an engine."""
    from datetime import UTC, datetime

    tmp_path.mkdir(parents=True, exist_ok=True)
    account = day_plan_account(tmp_path, now=now or datetime(2026, 10, 16, 0, 0, 30, tzinfo=UTC))
    if scan is not None:
        record_scan(account.database, scan, as_of=utc_iso(datetime(2026, 10, 15, 23, 0, tzinfo=UTC)))
    if switched_on:
        switch(account)
    transport, gateway, route = perplexity(account.database, account.clock, responses, key=key, paid=paid)
    scheduler, engine = engine_for(account, departments=departments, **engine_options)
    handler = FocusResearch(account.execution, gateway=gateway, route=route)
    return Research(account, scheduler, engine, transport, gateway, route, handler)


__all__ = ["BTC_NEWS", "KEY", "SHORTLIST", "SOL_NEWS", "Research", "ScriptedPerplexity", "finding", "fixture",
           "focus_answer", "inputs", "perplexity", "prompt", "record_scan", "ref_for", "research_setup", "result",
           "search", "shown", "switch", "tool_call"]
