"""HQ asks you when a bought coin has no safety exit (W10-C, C9; D274-D278).

Software puts a safety exit on every filled part of a day-plan buy in the transaction that records the fill (T34,
D117, D188). When it can't (a placement refused, the owner's cancel-all, a team that is off), the part stays bare
and the management step's sweep tries again. Once a coin has stayed without full cover for ``GRACE`` (10 minutes,
D275), "Needs you" shows one ``no_safety_exit`` card per coin: which coin, how much, since when, and what the system
is doing about it. The card clears on the next poll after the exit is in place or the position is closed.

How much is bare is the one shared arithmetic of ``kernel/day_plan_coverage.py`` (also used by
``DayPlanProtection.coverage`` and the day plans panel, ``api/hq_plans.py``), capped at coin level as the Trader's
own position view caps it (W8-B): never more than is held and not already protected, so coins sold another way (for
example by the sell-everything setting) need no card. Since when is the same arithmetic on a timeline: the fill whose
exit was never placed, or the moment an exit (or the Trader's close sell) that covered it ended unsold; the first
``protection_deferred`` event stands in when the records' times can't be read (D276).

Reading starts nothing: plain SQL inside the snapshot's read transaction (``hq.snapshot``), and the held quantity
from the ledger's books only when something is bare, cached behind the ledger's newest event. No execution, broker,
scheduler, engine or provider is imported or called; the card has no decision and no button that trades (D277).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from trade_graph.api import hq_copy as c
from trade_graph.api import hq_state
from trade_graph.api.hq_contract import OFFLINE_NOW_CODES
from trade_graph.api.hq_facts import parse
from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.domain.errors import TradeGraphError
from trade_graph.kernel.day_plan_coverage import (
    OUTSTANDING,
    Cover,
    coin_unprotected,
    counts_whole,
    shared_part,
    uncovered,
    uncovered_since,
)

GRACE: Final = timedelta(minutes=10)  # D275: a card only after 10 minutes without full cover
COIN_CARDS_MAX: Final = 3  # page bound (D116 is about activity, not cards): more coins share one "more" card
REQUIRED_TABLES: Final = ("day_plans", "day_plan_entries", "day_plan_exits", "day_plan_events")
ACTION_TABLE: Final = "day_plan_action_orders"  # migration 0044 (W8-B): the Trader's close sells
FAILURES: Final = (sqlite3.DatabaseError, ValueError, TypeError, ArithmeticError, LookupError, TradeGraphError)
ZERO: Final = Decimal("0")
P = c.PROTECTION
_SYMBOL = re.compile(SYMBOL_PATTERN)
_ENTRIES_OF = "JOIN day_plan_entries e ON e.entry_id = {alias}.entry_id JOIN day_plans p ON p.plan_id = e.plan_id"


@dataclass(frozen=True)
class CoinGap:
    """One coin held without full safety-exit cover: how much (in the coin's own units) and since when.

    ``since`` is ``None`` when the records' times can't be read; ``reason`` is the newest recorded reason a safety
    exit couldn't be placed since then (raw, shown only with "Show technical names").
    """

    symbol: str
    quantity: Decimal
    since: datetime | None
    reason: str | None


# --- reading the records -----------------------------------------------------------------------------------------


def _quantity(value: Any) -> Decimal:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return ZERO
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return ZERO
    return number if number.is_finite() else ZERO


def _fill_quantity(document_json: str) -> Decimal:
    try:
        document = json.loads(document_json)
    except (TypeError, ValueError):
        return ZERO
    return _quantity(document.get("quantity")) if isinstance(document, dict) else ZERO


def _fills(database, pid: str, wanted: set[str]) -> dict[str, list[tuple[datetime | None, Decimal]]]:
    """The fills of these orders as ``(recorded at, quantity)``, oldest first: one pass over the portfolio's fills
    (``fills`` has no index on its order), reading only the documents of the orders asked for."""
    found: dict[str, list[tuple[datetime | None, Decimal]]] = {}
    for row in database.execute("SELECT intent_id, created_at, document_json FROM fills WHERE portfolio_id = ? "
                                "ORDER BY rowid", (pid,)):
        if row["intent_id"] in wanted:
            found.setdefault(row["intent_id"], []).append((parse(row["created_at"]),
                                                           _fill_quantity(row["document_json"])))
    return found


def _sold(fills: list[tuple[datetime | None, Decimal]]) -> Decimal:
    return sum((quantity for _at, quantity in fills), ZERO)


def _cover(state: str | None, quantity: Decimal, sold: Decimal, placed: Any, updated: Any) -> Cover:
    return Cover(state, quantity, sold, placed_at=parse(placed),
                 ended_at=None if counts_whole(state) else parse(updated))


def read(runtime, now: datetime) -> list[CoinGap]:
    """Every coin of the paper portfolio held without full safety-exit cover, oldest gap first (reads only)."""
    database, pid = runtime.database, runtime.portfolio_id
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    if pid is None or not set(REQUIRED_TABLES) <= tables:
        return []
    entries = database.execute(
        """SELECT e.entry_id, e.intent_id, p.symbol FROM day_plan_entries e JOIN day_plans p ON p.plan_id = e.plan_id
           WHERE p.portfolio_id = ? ORDER BY e.sequence""", (pid,)).fetchall()
    if not entries:
        return []
    exit_rows = database.execute(
        f"""SELECT x.entry_id, x.intent_id, x.quantity, x.created_at, i.state, i.updated_at FROM day_plan_exits x
            {_ENTRIES_OF.format(alias="x")} LEFT JOIN order_intents i ON i.intent_id = x.intent_id
            WHERE p.portfolio_id = ? ORDER BY x.sequence""", (pid,)).fetchall()
    sell_rows = database.execute(
        f"""SELECT o.sequence, o.entry_id, o.intent_id, o.quantity, o.created_at, i.state, i.updated_at
            FROM day_plan_action_orders o {_ENTRIES_OF.format(alias="o")}
            LEFT JOIN order_intents i ON i.intent_id = o.intent_id
            WHERE o.role = 'sell' AND p.portfolio_id = ? ORDER BY o.sequence""",
        (pid,)).fetchall() if ACTION_TABLE in tables else []
    fills = _fills(database, pid, {row["intent_id"] for row in [*entries, *exit_rows, *sell_rows]})
    orders: dict[str, list[Cover]] = {}
    protected: dict[str, Decimal] = {}  # the Trader's view: what exits still out may sell, per entry
    for row in exit_rows:
        quantity, exit_sold = _quantity(row["quantity"]), _sold(fills.get(row["intent_id"], []))
        orders.setdefault(row["entry_id"], []).append(
            _cover(row["state"], quantity, exit_sold, row["created_at"], row["updated_at"]))
        if row["state"] in OUTSTANDING:
            protected[row["entry_id"]] = protected.get(row["entry_id"], ZERO) + max(ZERO, quantity - exit_sold)
    _add_close_sells(sell_rows, fills, orders)
    exposed: dict[str, list[tuple[str, Decimal, datetime | None]]] = {}
    coin_protected: dict[str, Decimal] = {}
    for entry in entries:
        symbol, bought = entry["symbol"], fills.get(entry["intent_id"], [])
        coin_protected[symbol] = coin_protected.get(symbol, ZERO) + protected.get(entry["entry_id"], ZERO)
        covers = orders.get(entry["entry_id"], [])
        bare = uncovered(_sold(bought), covers)
        if bare > 0:
            exposed.setdefault(symbol, []).append((entry["entry_id"], bare, _since(database, entry["entry_id"], bought,
                                                                                  covers)))
    if not exposed:
        return []
    held = _held(runtime)
    gaps = []
    for symbol, parts in exposed.items():
        exposure = sum((bare for _entry, bare, _since_at in parts), ZERO)
        base = symbol.split("/", 1)[0]
        quantity = exposure if held is None else coin_unprotected(
            exposure, held=held.get(base, ZERO), protected=coin_protected.get(symbol, ZERO))
        if quantity <= 0:
            continue
        starts = [since for _entry, _bare, since in parts if since is not None]
        since = min(starts) if starts and len(starts) == len(parts) else None
        gaps.append(CoinGap(symbol, quantity, since, _reason(database, [entry for entry, _b, _s in parts], since)))
    return sorted(gaps, key=lambda gap: (gap.since is not None, gap.since or now, gap.symbol))


def _add_close_sells(rows: list, fills: dict[str, list[tuple[datetime | None, Decimal]]],
                     orders: dict[str, list[Cover]]) -> None:
    """Each buy's parts of the Trader's close sells (W8-B), with their share of what the sale sold (D246)."""
    parts: dict[str, list[tuple[int, Decimal]]] = {}
    for row in rows:
        parts.setdefault(row["intent_id"], []).append((row["sequence"], _quantity(row["quantity"])))
    for row in rows:
        share = shared_part(_sold(fills.get(row["intent_id"], [])), parts[row["intent_id"]], row["sequence"])
        orders.setdefault(row["entry_id"], []).append(
            _cover(row["state"], _quantity(row["quantity"]), share, row["created_at"], row["updated_at"]))


def _since(database, entry_id: str, fills: list[tuple[datetime | None, Decimal]],
           orders: list[Cover]) -> datetime | None:
    """When the buy's bare part began: the shared timeline; else its first deferral after its newest fill (D276)."""
    times = [at for at, _quantity in fills]
    if all(at is not None for at in times):
        try:
            return uncovered_since([(at, quantity) for at, quantity in fills if at is not None], orders)
        except ValueError:
            pass
    newest = max((at for at in times if at is not None), default=None)
    for row in database.execute("SELECT created_at FROM day_plan_events WHERE entry_id = ? AND kind = "
                                "'protection_deferred' ORDER BY sequence", (entry_id,)).fetchall():
        moment = parse(row["created_at"])
        if moment is not None and (newest is None or moment >= newest):
            return moment
    return newest


def _reason(database, entry_ids: list[str], since: datetime | None) -> str | None:
    """The newest recorded reason a safety exit for these buys couldn't be placed, since the gap began."""
    marks = ",".join("?" for _ in entry_ids)
    for row in database.execute(
            f"""SELECT reason, created_at FROM day_plan_events WHERE kind = 'protection_deferred'
                AND entry_id IN ({marks}) ORDER BY sequence DESC LIMIT 50""", tuple(entry_ids)).fetchall():
        moment = parse(row["created_at"])
        if isinstance(row["reason"], str) and (since is None or (moment is not None and moment >= since)):
            return row["reason"]
    return None


def _held(runtime) -> dict[str, Decimal] | None:
    """Coins held per asset from the ledger's books, cached behind the portfolio's newest ledger event; ``None``
    when the books can't be read (then nothing is capped: an unknown holding never hides a bare coin)."""
    ledger = getattr(runtime, "ledger", None)
    if ledger is None:
        return None
    lock = getattr(runtime, "_hq_protection_lock", None)
    if lock is None:
        lock = runtime._hq_protection_lock = threading.Lock()
    with lock:
        try:
            mark = runtime.database.execute("SELECT MAX(rowid) FROM ledger_events").fetchone()[0]
            cached = getattr(runtime, "_hq_protection_held", None)
            if cached is not None and cached[0] == mark:
                return cached[1]
            held: dict[str, Decimal] = {}
            for lot in ledger.books(runtime.portfolio_id).lots:
                held[lot.asset] = held.get(lot.asset, ZERO) + lot.open_quantity()
        except FAILURES:
            return None
        runtime._hq_protection_held = (mark, held)
        return held


# --- the cards ----------------------------------------------------------------------------------------------------


def situation(p) -> str:
    """What the system is doing about a bare coin, from the projection's team state and pause (D277)."""
    if p.code in OFFLINE_NOW_CODES:
        return "team_off"
    return {"CANCEL_ALL": "cancel_all", "FLATTEN": "flatten", "STOPPED": "stopped"}.get(p.profile, "retrying")


def _symbol_suffix(symbol: str) -> str:
    return symbol if _SYMBOL.fullmatch(symbol) else re.sub(r"[^A-Za-z0-9/._-]", "_", symbol)[:24] or "coin"


def _card(gap: CoinGap, situation_: str, *, live: bool) -> dict:
    base = gap.symbol.split("/", 1)[0]
    title = c.says(P["title"].format(symbol=gap.symbol, quantity=c.quantity(gap.quantity), base=base),
                   c.term("safety_exit", P["safety_exit"]))
    why = c.says(P["why"], c.time(gap.since, "clock"), ".") if gap.since is not None else c.says(P["why_unknown"])
    how: list = c.says(P["doing"][situation_])
    if situation_ == "retrying" and gap.reason:
        how = c.says(*how, P["last_try"], c.reason_segment(gap.reason, live=live), ".")
    how = c.says(*how, P["how"])
    return hq_state._card("no_safety_exit", title, why=why, consequence=c.says(P["consequence"]), how=how,
                          link="/trading", since=gap.since, suffix=_symbol_suffix(gap.symbol))


def _more(gaps: list[CoinGap]) -> dict:
    coins = ", ".join(gap.symbol for gap in gaps)
    forms = P["more_title"]
    title = (forms[0] if len(gaps) == 1 else forms[1]).format(n=len(gaps), coins=coins)
    starts = [gap.since for gap in gaps if gap.since is not None]
    return hq_state._card("no_safety_exit", c.says(title), why=c.says(P["more_why"]), link="/trading",
                          since=min(starts) if len(starts) == len(gaps) else None, suffix="more")


def cards(gaps: list[CoinGap], now: datetime, *, situation: str, live: bool = False) -> list[dict]:
    """The "Needs you" cards: one per coin bare for at least ``GRACE`` (oldest first), at most ``COIN_CARDS_MAX``,
    then one card naming the rest. A gap whose start can't be read is shown at once (unknown is not "fine")."""
    due = [gap for gap in gaps if gap.since is None or now - gap.since >= GRACE]
    shown = [_card(gap, situation, live=live) for gap in due[:COIN_CARDS_MAX]]
    if len(due) > COIN_CARDS_MAX:
        shown.append(_more(due[COIN_CARDS_MAX:]))
    return shown


def attention(p, runtime, now: datetime) -> list[dict]:
    """The cards for ``hq.snapshot``; left out (never invented) if the records can't be read."""
    try:
        gaps = read(runtime, now)
    except FAILURES:
        return []
    return cards(gaps, now, situation=situation(p), live=not p.paper) if gaps else []


__all__ = ["COIN_CARDS_MAX", "GRACE", "CoinGap", "attention", "cards", "read", "situation"]
