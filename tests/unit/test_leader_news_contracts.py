"""The Leader's news-round contracts (T38; D121, D124). Synthetic values; no network, model, key or paid call.

What the Leader may return from a round is closed: headlines it read, deep-dive requests for Research, one optional
wake note for the Trader, strategy-level ideas for Research or Optimisation, and its next check-in. Nothing in the
contract can place, approve or block a trade, or change a strategy while the system runs.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.adapters.persistence import leader_news_schema
from trade_graph.contracts import leader_news
from trade_graph.contracts.leader_news import (
    MAX_ROUTED_REQUESTS,
    LeaderNewsReply,
    StrategyIdea,
    compose_note,
)
from trade_graph.contracts.wakes import MAX_NOTE_LENGTH, MAX_WAKE_REQUESTS
from trade_graph.contracts.web_research import WebResearchReply

ROOT = Path(__file__).resolve().parents[2]
REF = "web:" + "a" * 32 + ":1"
OTHER = "web:" + "b" * 32 + ":2"


def _reply(**changes) -> dict:
    document = {
        "summary": "Synthetic: Bitcoin steady; one exchange outage; a new AI trading-agent benchmark.",
        "headlines": [{"source_ref": REF, "takeaway": "Kraken reports SOL deposit delays.", "symbols": ["SOL/USD"]}],
        "deep_dives": [{"question": "Are SOL deposit delays at Kraken over?", "why": "We trade SOL today.",
                        "source_refs": [REF], "symbols": ["SOL/USD"]}],
        "trader_note": {"note": "Kraken reports SOL deposit delays; worth a look before your next check-in.",
                        "source_refs": [REF], "symbols": ["SOL/USD"]},
        "strategy_ideas": [{"department": "optimisation", "idea": "Test wider stops on outage days.",
                            "source_refs": [OTHER]}],
        "next_check_in": {"at_utc": "2026-10-16T14:00:00Z", "note": "after the US open"},
    }
    document.update(changes)
    return document


def test_a_round_reply_is_a_web_research_reply_and_names_its_next_check_in() -> None:
    reply = LeaderNewsReply.model_validate(_reply())
    assert isinstance(reply, WebResearchReply)
    assert reply.next_check_in.at_utc == datetime(2026, 10, 16, 14, tzinfo=UTC)
    assert reply.cited() == frozenset({REF, OTHER})
    # The next day start is a valid choice, but it has to be named (D123).
    assert LeaderNewsReply.model_validate(_reply(next_check_in={})).next_check_in.at_utc is None
    missing = _reply()
    del missing["next_check_in"]
    with pytest.raises(ValidationError, match="next_check_in"):
        LeaderNewsReply.model_validate(missing)


@pytest.mark.parametrize("field", ["orders", "order", "approve", "approval", "block", "veto", "day_plans", "pause",
                                   "cancel_orders", "mandate", "strategy_change", "config", "files", "focus"])
def test_nothing_in_a_reply_can_place_approve_or_block_a_trade_or_rewrite_the_system(field) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LeaderNewsReply.model_validate(_reply(**{field: {"symbol": "SOL/USD"}}))


def test_the_leader_skims_and_leaves_findings_to_research() -> None:
    finding = {"question": "q", "claim": "c", "source_ref": REF, "counterevidence": "x", "invalidation": "y",
               "expires_after_seconds": 3600}
    with pytest.raises(ValidationError, match="at most 0 items"):
        LeaderNewsReply.model_validate(_reply(findings=[finding]))
    assert LeaderNewsReply.model_validate(_reply(findings=[])).findings == []


def test_every_request_cites_stored_sources_and_texts_are_bounded_and_plain() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        LeaderNewsReply.model_validate(_reply(deep_dives=[{"question": "q", "why": "w", "source_refs": []}]))
    with pytest.raises(ValidationError, match="at least 1 item"):
        LeaderNewsReply.model_validate(_reply(trader_note={"note": "n", "source_refs": []}))
    with pytest.raises(ValidationError, match="String should match pattern"):
        LeaderNewsReply.model_validate(_reply(trader_note={"note": "n", "source_refs": ["not a ref"]}))
    with pytest.raises(ValidationError, match="at most 300 characters"):
        LeaderNewsReply.model_validate(_reply(trader_note={"note": "n" * 301, "source_refs": [REF]}))
    with pytest.raises(ValidationError, match="control or text-direction"):
        LeaderNewsReply.model_validate(_reply(summary="Synthetic‮summary"))
    with pytest.raises(ValidationError, match="String should match pattern"):
        LeaderNewsReply.model_validate(_reply(headlines=[{"source_ref": REF, "takeaway": "t", "symbols": ["SOL"]}]))


def test_strategy_ideas_go_only_to_research_or_optimisation() -> None:
    for department in ("research", "optimisation"):
        assert StrategyIdea(department=department, idea="Try it.", source_refs=[REF]).department == department
    for department in ("trader", "leader", "engineer", "owner"):
        with pytest.raises(ValidationError):
            StrategyIdea(department=department, idea="Try it.", source_refs=[REF])


def test_one_reply_fits_one_wake_outcome() -> None:
    """Size bounds of one document, like the wake contract's; they never limit how often the Leader runs (D116)."""
    assert MAX_ROUTED_REQUESTS <= MAX_WAKE_REQUESTS
    dive = {"question": "q", "why": "w", "source_refs": [REF]}
    with pytest.raises(ValidationError, match="at most 8 items"):
        LeaderNewsReply.model_validate(_reply(deep_dives=[dive] * 9))


def test_a_wake_note_always_fits_and_keeps_its_request_id_and_sources() -> None:
    note = compose_note("Leader deep dive ldr-1", [REF, OTHER], "q" * 400 + " Why: " + "w" * 400)
    assert len(note) == MAX_NOTE_LENGTH and note.endswith("…")
    assert note.startswith(f"Leader deep dive ldr-1 (sources {REF}, {OTHER}): ")
    assert compose_note("Leader note ldr-2", [REF], "Short.") == f"Leader note ldr-2 (sources {REF}): Short."
    # References that would crowd the text out stay in the stored request, not in the note.
    crowded = compose_note("Leader note ldr-3", ["r" * 128] * 3, "Keep the text.")
    assert crowded == "Leader note ldr-3: Keep the text."


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_reply_fits_strict_provider_reply_schemas(provider) -> None:
    reply = LeaderNewsReply.model_validate(_reply())
    wire = _wire_schema(LeaderNewsReply.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    document = reply.model_dump(mode="json")
    Draft202012Validator(wire).validate(document)
    assert LeaderNewsReply.model_validate(document) == reply
    quiet = LeaderNewsReply.model_validate(_reply(headlines=[], deep_dives=[], trader_note=None, strategy_ideas=[],
                                                  next_check_in={"at_utc": None}))
    Draft202012Validator(wire).validate(quiet.model_dump(mode="json"))


def test_the_store_accepts_exactly_the_contracts_values() -> None:
    """The migration's CHECK lists are frozen; a new status or kind in the contract needs a new migration."""
    for name in ("ROUND_STATUSES", "REQUEST_KINDS", "ROUTED_DEPARTMENTS"):
        assert getattr(leader_news_schema, name) == getattr(leader_news, name), name


@pytest.mark.parametrize("path", ["src/trade_graph/contracts/leader_news.py",
                                  "src/trade_graph/application/leader_news/rounds.py",
                                  "src/trade_graph/application/leader_news/brief.py",
                                  "src/trade_graph/application/leader_news/store.py",
                                  "src/trade_graph/adapters/persistence/leader_news_schema.py"])
def test_the_leader_news_round_is_a_protected_path(path) -> None:
    """It holds the Leader's authority boundary and its receipts: agents cannot edit it."""
    from trade_graph.kernel.authority import path_is_protected

    assert path_is_protected(path)


_NO_TRADE_ACCESS = ("trade_graph.application.execution", "trade_graph.application.day_plans",
                    "trade_graph.application.day_plan_protection", "trade_graph.application.ledger",
                    "trade_graph.application.money_limits", "trade_graph.application.trader",
                    "trade_graph.application.trader_workflow", "trade_graph.adapters.brokers",
                    "trade_graph.kernel.books", "trade_graph.kernel.fills", "trade_graph.application.events.engine")


@pytest.mark.parametrize("path", sorted((ROOT / "src" / "trade_graph" / "application" / "leader_news").glob("*.py"))
                         + [ROOT / "src" / "trade_graph" / "contracts" / "leader_news.py"],
                         ids=lambda path: path.name)
def test_the_leader_round_code_cannot_reach_orders_plans_or_the_books(path) -> None:
    """The round's code imports nothing that places, approves, blocks or installs an order (D121)."""
    imported = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported, path
    reached = [name for name in imported for banned in _NO_TRADE_ACCESS
               if name == banned or name.startswith(banned + ".")]
    assert reached == [], reached
