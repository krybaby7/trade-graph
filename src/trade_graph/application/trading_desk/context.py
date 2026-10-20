"""The Trading desk's ``playbook`` and ``news`` keys of a wake turn (W12-A, C12; D280, D295-D297).

``desk_context`` builds both from saved records, on any ``sqlite3.Row`` connection, inside the turn's snapshot:

- ``playbook``: the revision in force (per market type, the strategy versions the desk means to use or wait, with
  reasons and the reports they rest on), who set it, when and why, the revision before it, how many there are, and
  the desk's newest stance per coin of the last day, so it can see its own view and change it;
- ``news``: ``trading_desk.news.news_context`` (the Leader's newest round, the lab inbox, Jev's verdicts).

Each key keeps to its share of the 128 KiB request (``PLAYBOOK_CONTEXT_BYTES`` 6 KiB, ``NEWS_CONTEXT_BYTES`` 12 KiB):
older stances are left out first and counted. ``fit_desk_request`` is the last step of a desk turn's context: when
the whole request (context, instruction and reply schema) would still exceed the bound on a busy day, it trims the
desk's own parts in a fixed order (older news, stances, the market types of coins that are only on the shortlist,
older wake triggers, then less news) until it fits, and says what it left out. These bound what one turn is shown,
never what the desk may do (D116), and nothing here is a rule: software never refuses a day plan for not following
the playbook (D280 item 5).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from trade_graph.application.trading_desk.news import NEWS_CONTEXT_BYTES, fit_news, news_context
from trade_graph.application.trading_desk.store import read_latest_stances, read_playbook, read_playbook_revisions
from trade_graph.contracts.trading_desk import PlaybookRecord, StanceRecord
from trade_graph.domain.clock import utc_iso

PLAYBOOK_CONTEXT_BYTES = 6 * 1024  # the playbook's share of one turn's request; a bound on what is shown, not a cap
STANCES_SHOWN = 32
STANCE_HOURS = 24
PLAYBOOK_INSTRUCTION = (
    "current is your playbook in force: per market type, the strategy versions you mean to use or wait, why, and the "
    "reports it rests on. It is your own record, not a rule. To change it, answer with playbook: a whole new revision "
    "(choices, one per market type you cover, and why). recent_stances are your newest stances per coin.")


def _size(document: Any) -> int:
    return len(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode())


def _revision(record: PlaybookRecord) -> dict[str, Any]:
    return {"revision": record.revision, "revision_id": record.revision_id, "set_at": utc_iso(record.created_at),
            "set_by": record.set_by, "wake_id": record.wake_id, "why": record.why,
            "previous_revision_id": record.previous_revision_id,
            "choices": [choice.model_dump(mode="json") for choice in record.choices]}


def _stance(record: StanceRecord) -> dict[str, Any]:
    return {"symbol": record.symbol, "stance": record.stance, "market_type_seen": record.market_type_seen,
            "label": record.label_market_type if record.label_status == "current" else record.label_status,
            "strategy_ref": record.strategy_ref, "news_refs": list(record.news_refs), "reason": record.reason,
            "given_at": utc_iso(record.created_at)}


def playbook_context(connection: sqlite3.Connection, portfolio_id: str, *, at: datetime,
                     budget: int = PLAYBOOK_CONTEXT_BYTES) -> dict[str, Any]:
    """The desk's ``playbook`` key at ``at``. Reads only; a database before 0052 gives an empty key that says so."""
    available = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = "
                                    "'desk_playbook_revisions'").fetchone() is not None
    current = read_playbook(connection, portfolio_id, as_of=at)
    count = len(read_playbook_revisions(connection, portfolio_id, until=at)) if current is not None else 0
    previous = None
    if current is not None and current.previous_revision_id is not None:
        older = read_playbook(connection, portfolio_id, as_of=current.created_at - timedelta(microseconds=1))
        if older is not None and older.revision_id == current.previous_revision_id:
            previous = {"revision": older.revision, "set_at": utc_iso(older.created_at), "why": older.why}
    stances = sorted(read_latest_stances(connection, portfolio_id, as_of=at,
                                         since=at - timedelta(hours=STANCE_HOURS)).values(),
                     key=lambda item: item.created_at, reverse=True)
    document: dict[str, Any] = {
        "available": available, "current": None if current is None else _revision(current), "revisions": count,
        "previous": previous, "recent_stances": [_stance(item) for item in stances[:STANCES_SHOWN]],
        "recent_stances_omitted": max(0, len(stances) - STANCES_SHOWN), "instruction": PLAYBOOK_INSTRUCTION}
    return fit_playbook(document, budget)


def fit_playbook(document: dict[str, Any], budget: int) -> dict[str, Any]:
    """Leave out the oldest stances until the key fits (counted), then shorten long report lists. In place."""
    while _size(document) > budget and document["recent_stances"]:
        document["recent_stances"].pop()
        document["recent_stances_omitted"] += 1
    if _size(document) > budget and document["current"] is not None:  # an outsized playbook: shorter report lists
        for choice in document["current"]["choices"]:
            if len(choice["report_ids"]) > 3:
                choice["report_ids"] = choice["report_ids"][:3] + [f"and {len(choice['report_ids']) - 3} more"]
    return document


def _shortlist_only(wake: dict[str, Any]) -> None:
    """Keep the readings of held, planned, focus and trigger coins; shortlist-only coins keep their word on the
    shortlist itself (``wake.scan``), and their number is said."""
    readings = wake.get("market_types") or []
    kept = [item for item in readings if set(item.get("seen_as") or ()) - {"shortlist"}]
    wake["market_types"] = kept
    wake["market_types_omitted"] = wake.get("market_types_omitted", 0) + len(readings) - len(kept)


def _fewer_triggers(wake: dict[str, Any], shown: int) -> None:
    """The newest ``shown`` triggers only (every trigger stays in the wake record; ``triggers_total`` counts them)."""
    wake["triggers"] = list(wake.get("triggers") or ())[-shown:]


def fit_desk_request(context: dict[str, Any], room: int) -> list[str]:
    """Trim a desk wake turn's context in place until it is at most ``room`` bytes, in this order: older news (to
    half its share), older stances, the market types of shortlist-only coins, older wake triggers (to 20), less news
    (to a quarter), older wake triggers (to 10). Returns the steps taken; none when it already fits. What is left
    out is counted where it was (``*_omitted``, ``triggers_total``), never dropped silently."""
    wake = context.get("wake") if isinstance(context.get("wake"), dict) else None
    steps: list[tuple[str, Any]] = []
    if isinstance(context.get("news"), dict):
        steps.append(("news_half", lambda: fit_news(context["news"], NEWS_CONTEXT_BYTES // 2)))
    if isinstance(context.get("playbook"), dict):
        steps.append(("stances", lambda: fit_playbook(context["playbook"], PLAYBOOK_CONTEXT_BYTES // 2)))
    if wake is not None:
        steps.append(("shortlist_market_types", lambda: _shortlist_only(wake)))
        steps.append(("triggers_20", lambda: _fewer_triggers(wake, 20)))
    if isinstance(context.get("news"), dict):
        steps.append(("news_quarter", lambda: fit_news(context["news"], NEWS_CONTEXT_BYTES // 4)))
    if wake is not None:
        steps.append(("triggers_10", lambda: _fewer_triggers(wake, 10)))
    taken = []
    for name, step in steps:
        if request_size(context) <= room:
            break
        step()
        taken.append(name)
    return taken


def request_size(value: Any) -> int:
    """Bytes as the invocation journal's 128 KiB bound counts them (sorted keys, spaced separators, ASCII escapes):
    larger than the compact JSON the subscription bound reads, so a fit by this measure holds for both."""
    return len(json.dumps(value, sort_keys=True, default=str).encode())


def desk_context(connection: sqlite3.Connection, portfolio_id: str, *, at: datetime,
                 coins: Iterable[str]) -> dict[str, Any]:
    """Both desk keys of a wake turn: ``playbook`` and ``news`` (for the coins the wake is about)."""
    return {"playbook": playbook_context(connection, portfolio_id, at=at),
            "news": news_context(connection, portfolio_id, coins=coins, at=at, budget=NEWS_CONTEXT_BYTES)}


__all__ = ["PLAYBOOK_CONTEXT_BYTES", "PLAYBOOK_INSTRUCTION", "STANCES_SHOWN", "desk_context", "fit_desk_request",
           "fit_playbook", "playbook_context", "request_size"]
