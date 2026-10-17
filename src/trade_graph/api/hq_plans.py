"""HQ day plans (W8-C, D251): today's day plans per coin, with their waiting buys, fills, safety exits, refusals and
plan events, as ``trade-graph.hq.plans.v1`` (``api/hq_plans_contract.py``) from ``GET /api/v1/hq/plans``.

``plans(runtime)`` reads, inside one read snapshot of the state database, from saved records only:

- W4-A's day-plan records (state migration 0034): ``day_plans`` (the Trader's document, its coin and trading day, the
  plan it replaced), ``day_plan_entries`` (each waiting buy and its order intent), ``day_plan_exits`` (the safety
  exits software attached to filled parts) and ``day_plan_events`` (put in place, replaced, a buy cancelled and why,
  a safety exit that couldn't be placed yet);
- ``order_intents`` (each order's state) and ``fills`` (what each order bought or sold);
- the ``day_plan_refused`` activity records of today, and the newest owner policy (the trading-day start and whether
  money limits are recorded).

- W8-B's position actions (state migration 0044), when the database has them: ``day_plan_action_orders`` (each buy's
  part of a Trader's close sell) and the ``cancelling`` steps of ``day_plan_actions`` (which buys and safety exits a
  Trader's close or moved exit asked to cancel, so the panel can say why they were cancelled).

Which plans: each coin's newest plan whose trading day has not ended, and any older plan that still has an order open
(a safety exit protecting coins bought on an earlier day, a buy being checked, or a Trader's close sell still out),
newest first. How much of a filled buy has no safety exit right now is the same arithmetic as
``DayPlanProtection.coverage``: an exit still open or being checked covers its whole quantity, a sold exit what it
sold, an exit that ended unfilled only what it sold; a Trader's close sell counts the same way for each buy's part
(W8-B, D246: its part while outstanding, unknown or filled, else what it sold, shared out in order).

Reading starts nothing: plain SQL inside ``Database.snapshot()``, which ends in a rollback; no execution, broker,
scheduler, engine or provider is imported or called, so reading HQ never sends, cancels or places an order. A table
a database does not have yet reads as no day-plan records. Model text (the rationale and invalidation) appears only as
a labelled quote; every other word comes from ``hq_copy.PLANS``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Request

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import QUOTE_MAX_CHARS, iso_ms
from trade_graph.api.hq_facts import OPEN_ORDER_STATES
from trade_graph.api.hq_plans_contract import (
    ENTRIES_MAX,
    EVENTS_MAX,
    EXITS_MAX,
    FILLS_MAX,
    PLANS_MAX,
    REFUSALS_MAX,
    SCHEMA,
)
from trade_graph.api.security import redact
from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.money_limits import TradingDay, trading_day

P = c.PLANS
REQUIRED_TABLES = ("day_plans", "day_plan_entries", "day_plan_exits", "day_plan_events")
# The Trader's position actions (state migration 0044, W8-B): a database without them has no actions to show.
ACTION_TABLES = ("day_plan_actions", "day_plan_action_steps", "day_plan_action_orders")
DEFAULT_DAY_START = "00:00"  # D138 item 6, when the owner recorded no other trading-day start
ACTIVITY_SCAN_MAX = 200_000  # activity rows looked at, newest first, for today's refusals
REFUSAL_REASON_CHARS = 200
STATUS = {"SUBMISSION_PENDING": "sending", "SUBMITTING": "sending", "OPEN": "waiting",
          "PARTIALLY_FILLED": "partly_filled", "FILLED": "filled", "CANCELLED": "cancelled", "REJECTED": "rejected",
          "UNKNOWN": "being_checked", "CANCEL_PENDING": "being_checked"}
OUTSTANDING = frozenset({"SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_PENDING"})
ZERO = Decimal("0")
_SYMBOL = re.compile(SYMBOL_PATTERN)


# --- small helpers ------------------------------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str | int | Decimal) or isinstance(value, bool):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _amount(value: Any) -> Decimal:
    return _decimal(value) or ZERO


def _words(value: Any) -> str:
    """A price or quantity for a sentence: exact, with thousands separators."""
    number = _decimal(value)
    return P["unknown_amount"] if number is None else format(Decimal(canonical_decimal(number)), ",f")


def _forms(forms: tuple[str, str], count: int, **values: Any) -> str:
    return (forms[0] if count == 1 else forms[1]).format(n=count, **values)


def _base(symbol: str) -> str:
    return symbol.split("/", 1)[0]


def _quote_currency(symbol: str) -> str:
    return symbol.split("/", 1)[1] if "/" in symbol else ""


def _sentence(value: str) -> str:
    value = " ".join(value.split()).rstrip(".")
    return value[:1].upper() + value[1:] + "." if value else value


# --- reading the records ------------------------------------------------------------------------------------------


def _tables(database) -> set[str]:
    return {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _policy_settings(database) -> tuple[str, bool | None]:
    """(the trading-day start, whether money limits are recorded) from the newest owner policy, read as data."""
    row = database.execute(
        "SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        return DEFAULT_DAY_START, False
    try:
        document = json.loads(row["document_json"])
    except (TypeError, ValueError):
        return DEFAULT_DAY_START, None
    limits = document.get("money_limits") if isinstance(document, dict) else None
    start = limits.get("trading_day_start_utc") if isinstance(limits, dict) else None
    return (start if isinstance(start, str) else DEFAULT_DAY_START), isinstance(limits, dict)


def trading_day_of(database, now: datetime) -> TradingDay:
    start, _ = _policy_settings(database)
    try:
        return trading_day(now, start)
    except (ValueError, TradeGraphError):
        return trading_day(now, DEFAULT_DAY_START)


def _states(database, intent_ids: list[str]) -> dict[str, str]:
    if not intent_ids:
        return {}
    marks = ",".join("?" for _ in intent_ids)
    return {row["intent_id"]: row["state"] for row in database.execute(
        f"SELECT intent_id, state FROM order_intents WHERE intent_id IN ({marks})", tuple(intent_ids)).fetchall()}


def _fills(database, intent_ids: list[str]) -> dict[str, list[dict]]:
    if not intent_ids:
        return {}
    marks = ",".join("?" for _ in intent_ids)
    found: dict[str, list[dict]] = {}
    for row in database.execute(f"SELECT intent_id, document_json FROM fills WHERE intent_id IN ({marks}) "
                                "ORDER BY rowid", tuple(intent_ids)).fetchall():
        try:
            document = json.loads(row["document_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(document, dict):
            found.setdefault(row["intent_id"], []).append(document)
    return found


def read_plan(database, plan_id: str, now: datetime) -> dict:
    """One plan with its entries, their states, fills and exits, and its events, as plain values."""
    row = database.execute("SELECT * FROM day_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    superseded = database.execute("SELECT 1 FROM day_plans WHERE supersedes_plan_id = ?", (plan_id,)).fetchone()
    replaced = database.execute(
        """SELECT COUNT(*) FROM day_plans p WHERE p.portfolio_id = ? AND p.symbol = ? AND p.trading_day_start = ?
           AND p.plan_id <> ? AND EXISTS (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id = p.plan_id)""",
        (row["portfolio_id"], row["symbol"], row["trading_day_start"], plan_id)).fetchone()[0]
    entries = [dict(item) for item in database.execute(
        "SELECT * FROM day_plan_entries WHERE plan_id = ? ORDER BY position", (plan_id,)).fetchall()]
    exits = [dict(item) for item in database.execute(
        """SELECT x.* FROM day_plan_exits x JOIN day_plan_entries e ON e.entry_id = x.entry_id WHERE e.plan_id = ?
           ORDER BY x.sequence""", (plan_id,)).fetchall()]
    intents = [item["intent_id"] for item in entries] + [item["intent_id"] for item in exits]
    states, fills = _states(database, intents), _fills(database, intents)
    for item in exits:
        item.update(state=states.get(item["intent_id"]), fills=fills.get(item["intent_id"], []))
    for item in entries:
        item.update(state=states.get(item["intent_id"]), fills=fills.get(item["intent_id"], []),
                    exits=[exit_ for exit_ in exits if exit_["entry_id"] == item["entry_id"]])
    events = []
    for item in database.execute("SELECT * FROM day_plan_events WHERE plan_id = ? ORDER BY sequence",
                                 (plan_id,)).fetchall():
        try:
            document = json.loads(item["document_json"])
        except (TypeError, ValueError):
            document = {}
        events.append({"kind": item["kind"], "entry_id": item["entry_id"], "reason": item["reason"],
                       "document": document if isinstance(document, dict) else {}, "created_at": item["created_at"]})
    if set(ACTION_TABLES) <= _tables(database):
        _add_trader_actions(database, row["portfolio_id"], row["symbol"], entries, exits)
    try:
        document = json.loads(row["document_json"])
    except (TypeError, ValueError):
        document = {}
    return {"plan_id": plan_id, "symbol": row["symbol"], "installed_at": row["installed_at"],
            "day_start": row["trading_day_start"], "day_end": row["trading_day_end"],
            "strategy_ref": row["strategy_ref"], "document": document if isinstance(document, dict) else {},
            "current": superseded is None and row["trading_day_end"] > utc_iso(now), "replaced_today": replaced,
            "entries": entries, "events": events}


def _add_trader_actions(database, portfolio_id: str, symbol: str, entries: list[dict], exits: list[dict]) -> None:
    """Add the Trader's position actions (W8-B) to a plan's entries and exits, in place.

    Each entry gets ``sells``: its parts of a close's market sell, with the sell's state, fills, what it sold of this
    part (``sold``, the sell's fills shared out over its parts in their order, as ``DayPlanProtection._sold_part``)
    and the sell's whole quantity. Entries and exits that a close or a moved exit asked to cancel get ``cancelled_by``
    (``close`` or ``move_exit``), from the action's recorded ``cancelling`` steps.
    """
    by_entry = {item["entry_id"]: item for item in entries}
    for item in entries:
        item["sells"] = []
    if by_entry:
        marks = ",".join("?" for _ in by_entry)
        parts = [dict(row) for row in database.execute(
            f"""SELECT sequence, entry_id, intent_id, quantity, created_at FROM day_plan_action_orders
                WHERE role = 'sell' AND entry_id IN ({marks}) ORDER BY sequence""", tuple(by_entry)).fetchall()]
        sells = sorted({part["intent_id"] for part in parts})
        if sells:
            states, fills = _states(database, sells), _fills(database, sells)
            marks = ",".join("?" for _ in sells)
            every: dict[str, list[dict]] = {}
            for row in database.execute(
                    f"""SELECT sequence, intent_id, quantity FROM day_plan_action_orders
                        WHERE role = 'sell' AND intent_id IN ({marks}) ORDER BY sequence""", tuple(sells)).fetchall():
                every.setdefault(row["intent_id"], []).append(dict(row))
            for part in parts:
                intent_fills = fills.get(part["intent_id"], [])
                left, sold = _sold(intent_fills), ZERO
                for other in every.get(part["intent_id"], []):
                    share = min(left, _amount(other["quantity"]))
                    if other["sequence"] == part["sequence"]:
                        sold = share
                        break
                    left -= share
                by_entry[part["entry_id"]]["sells"].append(
                    {**part, "state": states.get(part["intent_id"]), "fills": intent_fills, "sold": sold,
                     "total": sum((_amount(other["quantity"]) for other in every.get(part["intent_id"], [])), ZERO)})
    wanted = {item["intent_id"]: item for item in [*entries, *exits]}
    for row in database.execute(
            """SELECT a.action, s.document_json FROM day_plan_action_steps s
               JOIN day_plan_actions a ON a.action_id = s.action_id
               WHERE a.portfolio_id = ? AND a.symbol = ? AND s.kind = 'cancelling' ORDER BY s.sequence""",
            (portfolio_id, symbol)).fetchall():
        try:
            document = json.loads(row["document_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        for key in ("entries", "exits"):
            named = document.get(key)
            for intent_id in named if isinstance(named, list) else []:
                if isinstance(intent_id, str) and intent_id in wanted:
                    wanted[intent_id]["cancelled_by"] = row["action"]


def _listed(database, portfolio_id: str, now: datetime) -> list[str]:
    """Each coin's newest plan whose day hasn't ended, then older plans that still have an order open; newest first."""
    current = [row["plan_id"] for row in database.execute(
        """SELECT plan_id FROM day_plans p WHERE portfolio_id = ? AND trading_day_end > ?
           AND NOT EXISTS (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id = p.plan_id)
           ORDER BY installed_at DESC, sequence DESC""", (portfolio_id, utc_iso(now))).fetchall()]
    marks = ",".join("?" for _ in OPEN_ORDER_STATES)
    holding = [row["plan_id"] for row in database.execute(
        f"""SELECT p.plan_id FROM day_plans p WHERE p.portfolio_id = ? AND (
              EXISTS (SELECT 1 FROM day_plan_entries e JOIN order_intents i ON i.intent_id = e.intent_id
                      WHERE e.plan_id = p.plan_id AND i.state IN ({marks}))
              OR EXISTS (SELECT 1 FROM day_plan_exits x JOIN day_plan_entries e ON e.entry_id = x.entry_id
                         JOIN order_intents i ON i.intent_id = x.intent_id
                         WHERE e.plan_id = p.plan_id AND i.state IN ({marks})))
            ORDER BY p.installed_at DESC, p.sequence DESC""",
        (portfolio_id, *sorted(OPEN_ORDER_STATES), *sorted(OPEN_ORDER_STATES))).fetchall()]
    if set(ACTION_TABLES) <= _tables(database):
        selling = [row["plan_id"] for row in database.execute(
            f"""SELECT DISTINCT p.plan_id FROM day_plans p JOIN day_plan_entries e ON e.plan_id = p.plan_id
                JOIN day_plan_action_orders o ON o.entry_id = e.entry_id AND o.role = 'sell'
                JOIN order_intents i ON i.intent_id = o.intent_id
                WHERE p.portfolio_id = ? AND i.state IN ({marks})""",
            (portfolio_id, *sorted(OPEN_ORDER_STATES))).fetchall()]
        if selling:  # a Trader's close sell still out (W8-B) keeps its plan listed
            holding = _newest_first(database, [*holding, *selling])
    return list(dict.fromkeys([*current, *holding]))


def _newest_first(database, plan_ids: list[str]) -> list[str]:
    """These plans, once each, newest first (as ``_listed`` orders them)."""
    unique = list(dict.fromkeys(plan_ids))
    if not unique:
        return []
    marks = ",".join("?" for _ in unique)
    return [row["plan_id"] for row in database.execute(
        f"""SELECT plan_id FROM day_plans WHERE plan_id IN ({marks}) ORDER BY installed_at DESC, sequence DESC""",
        tuple(unique)).fetchall()]


def _refusals(database, portfolio_id: str, since: datetime) -> list[dict]:
    """Today's ``day_plan_refused`` records, newest first (activity has no index on its kind: read newest first)."""
    cutoff = utc_iso(since)
    found = []
    rows = database.execute(
        """SELECT created_at, CASE WHEN kind = 'day_plan_refused' AND (portfolio_id = ? OR portfolio_id IS NULL)
           THEN payload_json END AS payload FROM activity_events ORDER BY rowid DESC LIMIT ?""",
        (portfolio_id, ACTIVITY_SCAN_MAX))
    for row in rows:
        if isinstance(row["created_at"], str) and row["created_at"] < cutoff:
            break
        if row["payload"] is None:
            continue
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("portfolio_id", portfolio_id) == portfolio_id:
            found.append({"created_at": row["created_at"], "payload": payload})
    return found


# --- the views ----------------------------------------------------------------------------------------------------


def _fill_line(fill: dict, base: str, quote: str) -> list[dict]:
    verb = P["bought"] if fill.get("side") == "buy" else P["sold"]
    fee = _decimal(fill.get("fee_amount"))
    words = [verb.format(quantity=_words(fill.get("quantity")), base=base, price=_words(fill.get("price")),
                         quote=quote)]
    at = fill.get("filled_at_utc")
    if isinstance(at, str):
        words += [P["at_time"], c.time(at, "clock")]
    if fee is not None and fee != 0:
        words.append(P["fee"].format(fee=_words(fee), asset=fill.get("fee_asset") or quote))
    return c.says(*words, ".")


def _fill_lines(fills: list[dict], base: str, quote: str) -> tuple[list, int]:
    lines = [_fill_line(fill, base, quote) for fill in fills[-FILLS_MAX:]]
    return lines, max(0, len(fills) - FILLS_MAX)


def _sold(fills: list[dict]) -> Decimal:
    return sum((_amount(fill.get("quantity")) for fill in fills), ZERO)


def _exit_view(item: dict, base: str, quote: str) -> dict:
    status = STATUS.get(item.get("state"), "being_checked")
    either = P["exit_either"].format(target=_words(item["take_profit_price"])) if item.get("take_profit_price") else ""
    words = P["exit"].format(quantity=_words(item["quantity"]), base=base, stop=_words(item["stop_price"]),
                             either=either)
    fills, more = _fill_lines(item.get("fills") or [], base, quote)
    tone = "good" if status == "filled" else "attention" if status in ("rejected", "being_checked") else "neutral"
    state_words = P["exit_status"][status]
    if status == "cancelled" and item.get("cancelled_by") in P["action_cancel"]:
        state_words += P["action_cancel"][item["cancelled_by"]]
    return {"id": item["exit_id"], "status": status, "tone": tone,
            "text": c.says(words, state_words, "."), "fills": fills, "more_fills": more,
            "_at": item.get("created_at") or ""}


def _sell_view(item: dict, base: str, quote: str) -> dict:
    """A buy's part of a Trader's close sell (W8-B), shown among the buy's safety exits."""
    status = STATUS.get(item.get("state"), "being_checked")
    words = P["close_sell"].format(quantity=_words(item["quantity"]), base=base)
    if item.get("total") is not None and _amount(item["total"]) != _amount(item["quantity"]):
        words += P["close_shared"].format(total=_words(item["total"]), base=base)
    fills, more = _fill_lines(item.get("fills") or [], base, quote)
    tone = "good" if status == "filled" else "attention" if status in ("rejected", "being_checked") else "neutral"
    return {"id": item["intent_id"], "status": status, "tone": tone,
            "text": c.says(words, P["close_status"][status], "."), "fills": fills, "more_fills": more,
            "_at": item.get("created_at") or ""}


def _sell_covers(item: dict) -> Decimal:
    """What a close sell's part covers: all of it while outstanding, unknown or filled, else what it sold (D246)."""
    state = item.get("state")
    return _amount(item["quantity"]) if state in OUTSTANDING or state == "FILLED" else _amount(item.get("sold"))


def _cancel_reasons(events: list[dict]) -> dict[str, str]:
    return {item["entry_id"]: item["reason"] for item in events
            if item["kind"] == "entry_cancelled" and item.get("entry_id")}


def entry_view(item: dict, base: str, quote: str, cancelled: dict[str, str]) -> dict:
    fills = item.get("fills") or []
    filled = _sold(fills)
    exits = item.get("exits") or []
    covered = ZERO
    for exit_ in exits:
        covered += _amount(exit_["quantity"]) if exit_.get("state") in OUTSTANDING or exit_.get("state") == "FILLED" \
            else _sold(exit_.get("fills") or [])
    sells = item.get("sells") or []
    covered += sum((_sell_covers(sell) for sell in sells), ZERO)
    unprotected = max(ZERO, filled - covered)
    status = STATUS.get(item.get("state"), "being_checked")
    target = P["target"].format(target=_words(item["take_profit_price"])) if item.get("take_profit_price") else ""
    words = [P["buy"].format(quantity=_words(item["quantity"]), base=base, price=_words(item["limit_price"]),
                             quote=quote, stop=_words(item["stop_price"]), target=target)]
    if status in ("sending", "waiting", "partly_filled", "being_checked"):
        words += [P["until"], c.time(item["expires_at"], "clock")]
    state_words = P["entry_status"][status]
    if status == "partly_filled":
        state_words = state_words.format(filled=_words(filled), quantity=_words(item["quantity"]))
    elif status == "cancelled":
        reason = cancelled.get(item["entry_id"]) or ("trader_close" if item.get("cancelled_by") == "close" else None)
        state_words += P["cancel_reason"].get(reason, "") if reason else ""
        if filled > 0:
            state_words += P["cancelled_after"].format(filled=_words(filled), base=base)
    words += [": ", state_words, "."]
    if unprotected > 0:
        words.append(P["unprotected"].format(quantity=_words(unprotected), base=base))
    tone = ("attention" if unprotected > 0 or status in ("rejected", "being_checked")
            else "good" if status == "filled" else "neutral")
    lines, more = _fill_lines(fills, base, quote)
    exit_views = [_exit_view(exit_, base, quote) for exit_ in exits] + [_sell_view(sell, base, quote) for sell in sells]
    exit_views.sort(key=lambda view: view.pop("_at"))  # oldest first; a close's sell among the exits by its time
    return {"id": item["entry_id"], "status": status, "tone": tone, "text": c.says(*words),
            "quantity": canonical_decimal(Decimal(item["quantity"])), "filled": canonical_decimal(filled),
            "unprotected": canonical_decimal(unprotected),
            "limit_price": canonical_decimal(Decimal(item["limit_price"])),
            "stop_price": canonical_decimal(Decimal(item["stop_price"])),
            "take_profit_price": canonical_decimal(Decimal(item["take_profit_price"]))
            if item.get("take_profit_price") else None,
            "expires_at": iso_ms(item["expires_at"]), "fills": lines, "more_fills": more,
            "exits": exit_views[-EXITS_MAX:], "more_exits": max(0, len(exit_views) - EXITS_MAX)}


def _event_line(event: dict, positions: dict[str, int]) -> list[dict]:
    kind, at = event["kind"], event["created_at"]
    if kind == "installed":
        return c.says(P["event"]["installed"], c.time(at, "clock"), ".")
    if kind == "superseded":
        return c.says(P["event"]["superseded"], c.time(at, "clock"), ".")
    number = positions.get(event.get("entry_id"), 0) + 1
    if kind == "entry_cancelled":
        reason = P["cancel_reason"].get(event.get("reason"), "")
        return c.says(P["event"]["cancelled"].format(n=number), c.time(at, "clock"), reason, ".")
    if kind == "protection_deferred":
        return c.says(P["event"]["deferred"].format(n=number), c.time(at, "clock"), P["event"]["deferred_tail"])
    return c.says(P["event"]["other"], c.time(at, "clock"), ".")


def _watching(document: dict) -> list[dict]:
    indicators = []
    for item in document.get("indicators") or []:
        if isinstance(item, dict) and isinstance(item.get("indicator"), str):
            minutes = item.get("interval_minutes")
            label = item["indicator"] + (P["interval"].format(n=minutes) if isinstance(minutes, int) else "")
            if label not in indicators:
                indicators.append(label)
    alarms = len(document.get("alarms") or [])
    parts = []
    if indicators:
        parts.append(P["watching"].format(names=", ".join(indicators)))
    if alarms:
        parts.append(_forms(P["alarms"], alarms))
    return c.says("; ".join(parts) + ".") if parts else []


def _strategy(ref: str) -> list[dict]:
    name, _, version = ref.partition("@")
    return c.says(P["strategy"].format(name=name, version=version or "?"))


def _plan_headline(view_entries: list[dict], current: bool) -> list[dict]:
    counts: dict[str, int] = {}
    for item in view_entries:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    protected = sum(1 for item in view_entries if item["status"] in ("filled", "partly_filled")
                    and Decimal(item["unprotected"]) == 0)
    unprotected = sum(1 for item in view_entries if Decimal(item["unprotected"]) > 0)
    parts = []
    for status in ("waiting", "sending", "partly_filled", "filled", "being_checked", "cancelled", "rejected"):
        if counts.get(status):
            parts.append(_forms(P["count"][status], counts[status]))
    if protected:
        parts.append(_forms(P["count"]["protected"], protected))
    if unprotected:
        parts.append(_forms(P["count"]["unprotected"], unprotected))
    head = P["no_buys"] if not view_entries else ", ".join(parts)
    return c.says(_sentence(head) + ("" if current else P["earlier_day"]))


def plan_view(record: dict, now: datetime) -> dict:
    """The ``PlanView`` of one plan as ``read_plan`` returns it (plain values, no database)."""
    symbol = record["symbol"]
    base, quote = _base(symbol), _quote_currency(symbol)
    document = record.get("document") or {}
    cancelled = _cancel_reasons(record["events"])
    entries = [entry_view(item, base, quote, cancelled) for item in record["entries"][:ENTRIES_MAX]]
    positions = {item["entry_id"]: item.get("position", 0) for item in record["entries"]}
    events = [_event_line(item, positions) for item in record["events"]]
    return {"id": record["plan_id"], "symbol": symbol, "installed_at": iso_ms(record["installed_at"]),
            "day_start": iso_ms(record["day_start"]), "day_end": iso_ms(record["day_end"]),
            "current": bool(record["current"]), "replaced_today": int(record.get("replaced_today") or 0),
            "headline": _plan_headline(entries, bool(record["current"])),
            "strategy": _strategy(str(record.get("strategy_ref") or "")),
            "watching": _watching(document),
            "quote": c.quote(document.get("rationale"), QUOTE_MAX_CHARS),
            "invalidation": c.quote(document.get("invalidation"), QUOTE_MAX_CHARS),
            "entries": entries, "events": events[-EVENTS_MAX:], "more_events": max(0, len(events) - EVENTS_MAX)}


def refusal_words(reason: str) -> str:
    """A recorded refusal reason in plain words: the day-plan checks first, then HQ's usual order refusals."""
    for marker, key in P["refusal_markers"]:
        if marker in reason:
            return P["refusal"][key]
    return c.reason_plain(reason) or P["refusal"]["unknown"]


def refusal_view(item: dict) -> dict:
    payload = item["payload"]
    symbol = payload.get("symbol")
    symbol = symbol if isinstance(symbol, str) and _SYMBOL.fullmatch(symbol) else None
    reason = str(payload.get("reason") or "")
    words = P["refused"].format(symbol=symbol or P["a_coin"], why=refusal_words(reason))
    return {"at": iso_ms(item["created_at"]), "symbol": symbol, "text": c.says(words),
            "technical": c.technical(reason, REFUSAL_REASON_CHARS) if reason else None}


def _headline(*, limits_recorded: bool | None, plans: list[dict], refusals_total: int) -> list[dict]:
    current = [item for item in plans if item["current"]]
    if limits_recorded is False and not plans:
        words = P["limits_off"]
    elif not current:
        words = P["none_today"]
    else:
        waiting = sum(1 for item in current for entry in item["entries"] if entry["status"] in ("waiting", "sending"))
        filled = sum(1 for item in current for entry in item["entries"] if entry["status"] in ("filled",
                                                                                                "partly_filled"))
        words = _forms(P["today"], len(current), coins=", ".join(item["symbol"] for item in current))
        details = []
        if waiting:
            details.append(_forms(P["count"]["waiting"], waiting))
        if filled:
            details.append(_forms(P["count"]["bought"], filled))
        words += (": " + ", ".join(details) + ".") if details else "."
    if refusals_total:
        words += " " + _forms(P["refused_today"], refusals_total)
    return c.says(words)


def document(*, now: datetime, day: TradingDay, limits_recorded: bool | None, plans: list[dict], more_plans: int,
             refusals: list[dict], refusals_total: int) -> dict:
    return {"schema": SCHEMA, "as_of": iso_ms(now), "available": True, "status_text": None,
            "day": {"start": iso_ms(day.start), "end": iso_ms(day.end)}, "limits_recorded": limits_recorded,
            "headline": _headline(limits_recorded=limits_recorded, plans=plans, refusals_total=refusals_total),
            "plans": plans, "more_plans": more_plans,
            "refusals": [refusal_view(item) for item in refusals[:REFUSALS_MAX]], "refusals_total": refusals_total}


def plans(runtime) -> dict:
    """Today's day plans document (reads only)."""
    with runtime.database.snapshot():
        now = runtime.clock.now()
        database, pid = runtime.database, runtime.portfolio_id
        if not set(REQUIRED_TABLES) <= _tables(database):
            return {"schema": SCHEMA, "as_of": iso_ms(now), "available": False,
                    "status_text": c.says(P["unavailable"]), "day": None, "limits_recorded": None,
                    "headline": c.says(P["unavailable"]), "plans": [], "more_plans": 0, "refusals": [],
                    "refusals_total": 0}
        day = trading_day_of(database, now)
        _, limits_recorded = _policy_settings(database)
        listed = _listed(database, pid, now)
        views = [plan_view(read_plan(database, plan_id, now), now) for plan_id in listed[:PLANS_MAX]]
        refusals = _refusals(database, pid, day.start)
        return document(now=now, day=day, limits_recorded=limits_recorded, plans=views,
                        more_plans=max(0, len(listed) - PLANS_MAX), refusals=refusals, refusals_total=len(refusals))


def register(app, runtime, identity) -> None:
    """Add ``GET /api/v1/hq/plans`` (any signed-in role; reads only)."""

    @app.get("/api/v1/hq/plans")
    def hq_plans(request: Request):
        identity(request)
        return redact(plans(runtime))


__all__ = ["document", "entry_view", "plan_view", "plans", "read_plan", "refusal_view", "refusal_words", "register",
           "trading_day_of"]
