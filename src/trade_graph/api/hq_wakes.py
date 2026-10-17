"""HQ wake log (T39, D215-D219): every wake-up with its reasons, department and allowance reading, each department's
next check-in, today's steps, the day's focus coins, the alarms that are set and the wake counts.

``wake_log(runtime, before=None, limit=PAGE_DEFAULT)`` builds one ``trade-graph.hq.wakes.v1`` document
(``api/hq_wakes_contract.py``) inside one read snapshot of the state database, from saved records only:

- the event engine's records (state migration 0035, W5-A): ``wakes`` (with what each wake was handed and the allowance
  reading saved with it, both immutable), ``wake_triggers``, ``department_check_ins``, ``focus_selections`` and
  ``day_cycle_steps``;
- the alarm book (migration 0029, W1-D): ``price_alarms`` with ``price_alarm_watch`` (alarms that are set) and
  ``alarm_deliveries`` (alarms that went off and wait for their department's next wake-up);
- ``tasks`` (whether a woken department's turn is running) and the newest owner policy (the trading-day start).

Reading starts nothing (D219): plain SQL inside ``Database.snapshot()``, which ends in a rollback; no event engine,
scheduler, worker, provider or broker is imported or called, so reading HQ never makes a wake, a task, a model call or
an order. Each department's next check-in follows ``EventEngine.next_check_ins`` (D123): its newest check-in when it
named a time that has not come yet, otherwise the next day start. A table a database does not have yet reads as no
records; a database from before migration 0035 has no wake log and says so. Every word comes from ``hq_copy.WAKES``;
model text (summaries, notes) appears only as a short labelled quote.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import HTTPException, Query, Request

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import iso_ms
from trade_graph.api.hq_wakes_contract import (
    ALARMS_LISTED_MAX,
    EVENT_LINES_MAX,
    FOCUS_COINS_MAX,
    NOTES_MAX,
    OPEN_STATUSES,
    PAGE_DEFAULT,
    PAGE_MAX,
    SCHEMA,
    SHORTLIST_MAX,
)
from trade_graph.api.security import redact
from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.indicators import get_indicator
from trade_graph.contracts.wakes import CYCLE_STEPS, DEPARTMENTS, TRIGGER_KINDS
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.money_limits import TradingDay, trading_day

W = c.WAKES
REQUIRED_TABLES = ("wakes", "wake_triggers", "department_check_ins", "focus_selections", "day_cycle_steps")
WEEK = timedelta(days=7)
DEFAULT_DAY_START = "00:00"  # D138 item 6, when the owner recorded no other trading-day start
RUNNING_TASKS = frozenset({"LEASED", "RUNNING"})
LATE_AFTER_SECONDS = 300  # a day start recorded later than this says how late the team started
NOTE_QUOTE_CHARS = 120  # notes handed to a wake are quoted shorter than summaries, so a busy page stays in budget
_SYMBOL = re.compile(SYMBOL_PATTERN)
WakesBefore = Annotated[int | None, Query(ge=1)]
WakesLimit = Annotated[int, Query(ge=1, le=PAGE_MAX)]


# --- small helpers --------------------------------------------------------------------------------------------


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _loads(value: Any) -> dict[str, Any]:
    try:
        document = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def _plural(forms: tuple[str, str], count: int, **values: Any) -> str:
    return (forms[0] if count == 1 else forms[1]).format(n=count, **values)


def _capital(words: str) -> str:
    return words[:1].upper() + words[1:]


def _who(name: Any) -> str:
    return W["who"].get(name, W["who"]["other"]) if isinstance(name, str) else W["who"]["other"]


def _symbol(value: Any) -> str | None:
    return value if isinstance(value, str) and _SYMBOL.fullmatch(value) else None


def _amount(value: Any) -> str:
    """A saved exact decimal for a sentence, as saved (already canonical); anything else reads as unknown."""
    return value if isinstance(value, str) and value else "an unknown amount"


def _quote(value: Any, limit: int = c.QUOTE_MAX_CHARS) -> dict | None:
    return c.quote(value, limit) if isinstance(value, str) and value.strip() else None


def _sha256(document: Any) -> str:
    """The store's check value of a focus selection (``events.store.sha256_of``: canonical JSON, SHA-256)."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _percent(value: Any) -> str | None:
    """An allowance percent as saved (exact decimal text), or None."""
    number = c.decimal_or_none(value)
    if number is None or not 0 <= number <= 100:
        return None
    return canonical_decimal(number)


def alarm_words(condition: dict) -> str:
    """An alarm's condition in words, as the price charts word it (T41): "mid price crosses below 95"."""
    relation = W["relation"].get(condition.get("relation"), str(condition.get("relation")))
    level = _amount(condition.get("level"))
    kind = condition.get("kind")
    if kind == "quote_price":
        return f"{W['quote_price'].get(condition.get('price', 'mid'), 'price')} {relation} {level}"
    interval = W["interval"].get(condition.get("interval_minutes"), "")
    if kind == "candle_price":
        return W["candle"].format(interval=interval, price=W["candle_price"].get(condition.get("price", "close"),
                                                                                  "close"),
                                  relation=relation, level=level).strip()
    name = str(condition.get("indicator"))
    parameters = dict(condition.get("parameters") or {})
    try:
        spec = get_indicator(name)
        values = [str(parameters[item.name]) for item in spec.parameters if item.name in parameters]
        outputs = spec.output_names
    except (TradeGraphError, ValueError, KeyError):
        values, outputs = [], ()
    label = f"{name} {', '.join(values)}" if values else name
    output = condition.get("output")
    named = f" {str(output).replace('_', ' ')}" if output and len(outputs) > 1 else ""
    return W["indicator"].format(label=label, output=named, interval=interval, relation=relation, level=level)


# --- reading the records ---------------------------------------------------------------------------------------


class _Reader:
    """The state database's wake records, read inside the request's snapshot. Every query is bounded."""

    def __init__(self, database, portfolio_id: str, now: datetime) -> None:
        self.database = database
        self.pid = portfolio_id
        self.now = now
        self.tables = {row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}

    def has(self, *names: str) -> bool:
        return all(name in self.tables for name in names)

    @property
    def available(self) -> bool:
        return self.has(*REQUIRED_TABLES)

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list:
        return self.database.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()):
        return self.database.execute(sql, tuple(params)).fetchone()

    # --- the day

    def day(self) -> TradingDay:
        """Today's trading day: the one whose start was recorded, else the owner's day start, else 00:00 UTC."""
        now = utc_iso(self.now)
        row = self.one("""SELECT trading_day_start, trading_day_end FROM day_cycle_steps WHERE portfolio_id = ?
                          AND step = 'day_start' AND trading_day_start <= ? AND trading_day_end > ?
                          ORDER BY trading_day_start DESC LIMIT 1""", (self.pid, now, now))
        start, end = (_parse(row[0]), _parse(row[1])) if row else (None, None)
        if start is not None and end is not None and start <= self.now < end:
            return TradingDay(start, end)
        try:
            return trading_day(self.now, self.day_start_setting())
        except (ValueError, TradeGraphError):
            return trading_day(self.now, DEFAULT_DAY_START)

    def day_start_setting(self) -> str:
        if not self.has("owner_policy_revisions"):
            return DEFAULT_DAY_START
        row = self.one("SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1")
        limits = _loads(row[0]).get("money_limits") if row else None
        start = limits.get("trading_day_start_utc") if isinstance(limits, dict) else None
        return start if isinstance(start, str) else DEFAULT_DAY_START

    # --- wakes

    def page(self, before: int | None, limit: int) -> list:
        return self.rows("""SELECT * FROM wakes WHERE portfolio_id = ? AND (? IS NULL OR sequence < ?)
                            ORDER BY sequence DESC LIMIT ?""", (self.pid, before, before, limit))

    def older(self, cursor: int) -> bool:
        return self.one("SELECT 1 FROM wakes WHERE portfolio_id = ? AND sequence < ? LIMIT 1",
                        (self.pid, cursor)) is not None

    def total(self) -> int:
        return int(self.one("SELECT COUNT(*) FROM wakes WHERE portfolio_id = ?", (self.pid,))[0])

    def open_wakes(self) -> list:
        return self.rows("""SELECT * FROM wakes WHERE portfolio_id = ? AND status IN ('queued', 'applying')
                            ORDER BY sequence""", (self.pid,))

    def task_states(self, task_ids: Iterable[str | None]) -> dict[str, str]:
        wanted = sorted({task for task in task_ids if task})
        if not wanted or not self.has("tasks"):
            return {}
        marks = ",".join("?" for _ in wanted)
        return {row["task_id"]: row["status"] for row in self.rows(
            f"SELECT task_id, status FROM tasks WHERE task_id IN ({marks})", wanted)}

    def check_ins(self, check_in_ids: Iterable[str | None]) -> dict[str, dict]:
        wanted = sorted({item for item in check_in_ids if item})
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        return {row["check_in_id"]: dict(row) for row in self.rows(
            f"SELECT * FROM department_check_ins WHERE check_in_id IN ({marks})", wanted)}

    def focus_coins(self, focus_ids: Iterable[Any]) -> dict[str, int]:
        """How many coins each recorded focus selection names."""
        wanted = sorted({item for item in focus_ids if isinstance(item, str) and item})
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        return {row["focus_id"]: len(_loads(row["document_json"]).get("coins") or []) for row in self.rows(
            f"SELECT focus_id, document_json FROM focus_selections WHERE focus_id IN ({marks})", wanted)}

    def trigger_departments(self, trigger_ids: Iterable[str]) -> dict[str, str]:
        wanted = sorted(set(trigger_ids))
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        return {row["trigger_id"]: row["department"] for row in self.rows(
            f"SELECT trigger_id, department FROM wake_triggers WHERE trigger_id IN ({marks})", wanted)}

    def alarm_conditions(self, alarm_ids: Iterable[str]) -> dict[str, dict]:
        wanted = sorted(set(alarm_ids))
        if not wanted or not self.has("price_alarms"):
            return {}
        marks = ",".join("?" for _ in wanted)
        return {row["alarm_id"]: _loads(row["condition_json"]) for row in self.rows(
            f"SELECT alarm_id, condition_json FROM price_alarms WHERE alarm_id IN ({marks})", wanted)}

    # --- check-ins, steps, focus, alarms, counts

    def latest_check_in(self, department: str):
        return self.one("""SELECT * FROM department_check_ins WHERE portfolio_id = ? AND department = ?
                           ORDER BY sequence DESC LIMIT 1""", (self.pid, department))

    def check_in_fired(self, check_in_id: str) -> bool:
        return self.one("SELECT 1 FROM wake_triggers WHERE source_ref = ?", (f"check-in:{check_in_id}",)) is not None

    def waiting(self) -> dict[str, int]:
        return {row[0]: int(row[1]) for row in self.rows(
            """SELECT department, COUNT(*) FROM wake_triggers WHERE portfolio_id = ? AND status = 'pending'
               GROUP BY department""", (self.pid,))}

    def steps(self, day: TradingDay) -> dict[str, dict]:
        return {row["step"]: {"status": row["status"], "detail": _loads(row["detail_json"]),
                              "created_at": row["created_at"], "updated_at": row["updated_at"]}
                for row in self.rows("""SELECT step, status, detail_json, created_at, updated_at FROM day_cycle_steps
                                        WHERE portfolio_id = ? AND trading_day_start = ?""",
                                     (self.pid, utc_iso(day.start)))}

    def focus(self, day: TradingDay):
        return self.one("""SELECT * FROM focus_selections WHERE portfolio_id = ? AND trading_day_start = ?
                           ORDER BY revision DESC LIMIT 1""", (self.pid, utc_iso(day.start)))

    def armed_alarms(self) -> tuple[int, list]:
        if not self.has("price_alarms", "price_alarm_watch"):
            return 0, []
        where = "w.status = 'armed' AND (a.portfolio_id IS NULL OR a.portfolio_id = ?)"
        total = int(self.one(f"""SELECT COUNT(*) FROM price_alarms a JOIN price_alarm_watch w USING (alarm_id)
                                 WHERE {where}""", (self.pid,))[0])
        rows = self.rows(f"""SELECT a.alarm_id, a.department, a.symbol, a.condition_json, a.note, a.day_plan_ref,
                             w.armed_at FROM price_alarms a JOIN price_alarm_watch w USING (alarm_id) WHERE {where}
                             ORDER BY a.symbol, a.sequence LIMIT ?""", (self.pid, ALARMS_LISTED_MAX))
        return total, rows

    def fired_waiting(self) -> dict[str, int]:
        if not self.has("alarm_deliveries"):
            return {}
        return {row[0]: int(row[1]) for row in self.rows(
            """SELECT department, COUNT(*) FROM alarm_deliveries WHERE status = 'pending' AND kind = 'alarm_fired'
               AND (portfolio_id IS NULL OR portfolio_id = ?) GROUP BY department""", (self.pid,))}

    def counts(self, since: datetime) -> dict[str, int]:
        return {row[0]: int(row[1]) for row in self.rows(
            "SELECT department, COUNT(*) FROM wakes WHERE portfolio_id = ? AND created_at >= ? GROUP BY department",
            (self.pid, utc_iso(since)))}

    def reasons(self, since: datetime) -> dict[str, int]:
        return {row[0]: int(row[1]) for row in self.rows(
            """SELECT t.kind, COUNT(*) FROM wake_triggers t JOIN wakes w ON w.wake_id = t.wake_id
               WHERE w.portfolio_id = ? AND w.created_at >= ? GROUP BY t.kind""", (self.pid, utc_iso(since)))}


# --- one wake --------------------------------------------------------------------------------------------------


def _reasons(context: dict) -> list[dict]:
    counted = Counter(trigger.get("kind") for trigger in context.get("triggers") or [] if isinstance(trigger, dict))
    listed = [kind for kind in context.get("reasons") or [] if kind in TRIGGER_KINDS]
    kinds = [kind for kind in TRIGGER_KINDS if kind in counted or kind in listed]
    return [{"kind": kind, "count": max(1, counted.get(kind, 0))} for kind in kinds]


def _headline(reasons: list[dict], context: dict) -> list:
    requesters = [trigger.get("requested_by") for trigger in context.get("triggers") or []
                  if isinstance(trigger, dict) and trigger.get("kind") == "wake_request"]
    parts = [_plural(W["reason"][item["kind"]], item["count"], who=_who(requesters[0] if requesters else None))
             for item in reasons]
    words = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + W["and"] + parts[-1]
    return c.says(_capital(words))


def _fill_words(template: str, trigger: dict, **extra: str) -> str:
    symbol = _symbol(trigger.get("symbol")) or "a coin"
    base, _, quote = symbol.partition("/")
    return template.format(quantity=_amount(trigger.get("quantity")), base=base, quote=quote or "",
                           price=_amount(trigger.get("price")), symbol=symbol, **extra)


def _event(trigger: dict, context: dict, deliveries: dict[str, dict], conditions: dict[str, dict],
           check_ins: dict[str, dict]) -> list:
    """One trigger in plain words."""
    kind = trigger.get("kind")
    event = W["event"]
    occurred = _parse(trigger.get("occurred_at"))
    if kind == "day_start":
        return c.says(event["day_start"][0], c.time(occurred, "day_clock") if occurred else None,
                      event["day_start"][1])
    if kind == "focus_step":
        shortlist = (context.get("scan") or {}).get("shortlist") or []
        return c.says(_plural(event["focus_step"], len(shortlist)) if shortlist else event["focus_step_none"])
    if kind == "day_plan_step":
        coins = [coin.get("symbol") for coin in (((context.get("focus") or {}).get("selection") or {})
                                                 .get("coins") or []) if isinstance(coin, dict)]
        coins = [symbol for symbol in coins if _symbol(symbol)]
        return c.says(event["day_plan_step_focus"].format(coins=", ".join(coins)) if coins
                      else event["day_plan_step"])
    if kind == "day_end_review":
        day = _parse(trigger.get("trading_day_start"))
        return c.says(event["day_end_review"][0], c.time(day, "date") if day else "the day before",
                      event["day_end_review"][1])
    if kind == "check_in":
        record = check_ins.get(trigger.get("check_in_id") or "")
        due = _parse(record["due_at"]) if record else occurred
        return c.says(event["check_in"][0], c.time(due, "day_clock") if due else None, event["check_in"][1])
    if kind == "alarm_fired":
        document = deliveries.get(trigger.get("delivery_id") or "", {})
        condition = document.get("condition") if isinstance(document.get("condition"), dict) else \
            conditions.get(trigger.get("alarm_id") or "")
        symbol = _symbol((condition or {}).get("symbol"))
        if not condition or symbol is None:
            return c.says(event["alarm_fired_unknown"])
        return c.says(event["alarm_fired"].format(symbol=symbol, condition=alarm_words(condition)))
    if kind in ("feed_lost", "feed_restored"):
        document = deliveries.get(trigger.get("delivery_id") or "", {})
        symbol = _symbol(document.get("symbol"))
        if symbol is None:
            return c.says(event[f"{kind}_unknown"])
        feed = W["feed"].get(document.get("feed"), W["feed"]["other"]).format(
            interval=W["interval"].get(document.get("interval_minutes"), ""))
        return c.says(event[kind].format(symbol=symbol, feed=feed.strip()))
    if kind == "entry_filled":
        return c.says(_fill_words(event["entry_filled"], trigger))
    if kind == "exit_filled":
        leg = W["leg"].get(trigger.get("exit_leg"))
        if leg is None:
            return c.says(_fill_words(event["exit_filled_unknown_leg"], trigger))
        return c.says(_fill_words(event["exit_filled"], trigger, leg=leg))
    if kind == "order_filled":
        return c.says(_fill_words(event["order_filled"], trigger,
                                  side=W["side"].get(trigger.get("side"), W["side"]["other"])))
    if kind == "wake_request":
        return c.says(_capital(event["wake_request"].format(who=_who(trigger.get("requested_by")))))
    if kind == "news_alarm":
        symbol = _symbol(trigger.get("symbol"))
        return c.says(event["news_alarm_symbol"].format(symbol=symbol) if symbol else event["news_alarm"])
    if kind == "task_created":
        return c.says(c.text(event["task_created"], code=c.technical(trigger.get("objective"))))
    return c.says(event.get(kind) or _capital(W["reason"].get(kind, ("a reason",))[0]) + ".")


def _allowance(document: dict) -> dict:
    status = document.get("status") if document.get("status") in ("available", "paused", "unknown") else "unknown"
    percent = _percent(document.get("lowest_remaining_percent"))
    observed = _parse(document.get("observed_at"))
    head = [c.term("allowance", W["allowance_term"]), W["allowance_head"]]
    if status == "paused":
        words = c.says(*head, W["allowance_paused"][0], c.term("stop_line", W["allowance_paused"][1]),
                       W["allowance_paused"][2])
        if percent is not None and observed is not None:
            words = c.says(*words, W["allowance_paused_left"][0].format(percent=c.pct(c.decimal_or_none(percent))),
                           c.time(observed, "day_clock"), W["allowance_paused_left"][1])
    elif status == "available" and observed is not None:
        if percent is not None:
            words = c.says(*head, W["allowance_left"][0].format(percent=c.pct(c.decimal_or_none(percent))),
                           c.time(observed, "day_clock"), W["allowance_left"][1])
        else:
            words = c.says(*head, W["allowance_no_amount"][0], c.time(observed, "day_clock"),
                           W["allowance_no_amount"][1])
    else:
        status, percent, observed = "unknown", None, None
        words = c.says(*head, W["allowance_unknown"])
    return {"status": status, "text": words, "remaining_percent": percent,
            "observed_at": iso_ms(observed) if observed else None}


def _result(row, outcome: dict, allowance: dict, task_state: str | None, lookups: _Lookups) -> list:
    """What came of a wake, from its status and the engine's record of the answer (``WakeOutcomeRecord``)."""
    status = row["status"]
    if status == "queued":
        if task_state in RUNNING_TASKS:
            return c.says(W["working"])
        return c.says(W["queued_paused"] if allowance["status"] == "paused" else W["queued"])
    if status == "applying":
        return c.says(W["applying"])
    if status == "failed":
        return c.says(c.text(W["failed_task"] if row["origin"] == "task" else W["failed"],
                             code=c.technical(row["close_reason"])))
    if status == "obsolete":
        return c.says(c.text(W["obsolete"], code=c.technical(row["close_reason"])))
    created, closed = _parse(row["created_at"]), _parse(row["closed_at"])
    parts: list = ([W["done"][0], c.duration((closed - created).total_seconds()), W["done"][1]]
                   if created and closed and closed >= created else [W["done_plain"]])
    record = lookups.check_ins.get(outcome.get("check_in_id") or "")
    due = _parse(record["due_at"]) if record else None
    if record and due is not None:
        words = W["check_in_named"] if record["basis"] == "named" else W["check_in_day_start"]
        parts += [words[0], c.time(due, "day_clock"), words[1]]
    coins = lookups.focus_coins.get(outcome.get("focus_id") or "")
    if coins is not None:
        parts.append(_plural(W["focus_named"], coins) if coins else W["focus_none"])
    if outcome.get("focus_refused"):
        parts.append(c.text(W["focus_refused"], code=c.technical(outcome.get("focus_refused"))))
    plans = [item for item in outcome.get("plans") or [] if isinstance(item, dict)]
    placed = sum(1 for item in plans if item.get("status") in ("installed", "existing"))
    refused = [item for item in plans if item.get("status") == "refused"]
    late = sum(1 for item in plans if item.get("status") == "obsolete")
    if placed:
        parts.append(_plural(W["plans_installed"], placed))
    if refused:
        codes = ", ".join(sorted({str(item.get("error_code") or "refused") for item in refused}))
        parts.append(c.text(_plural(W["plans_refused"], len(refused)), code=c.technical(codes)))
    if late:
        parts.append(_plural(W["plans_obsolete"], late))
    asked = list(dict.fromkeys(lookups.requested[trigger_id] for trigger_id in
                               outcome.get("requested_trigger_ids") or [] if trigger_id in lookups.requested))
    if asked:
        parts.append(W["asked"].format(who=_names(asked)))
    if outcome.get("refused"):
        parts.append(c.text(W["answer_refused"], code=c.technical(outcome.get("refused"))))
    return c.says(*parts)


def _names(departments: list[str]) -> str:
    names = [_who(name) for name in departments]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + W["and"] + names[-1]


def _notes(context: dict, lookups: _Lookups) -> list[dict]:
    """Notes the wake was handed, quoted as data: wake requests, news alarms and its own check-in note."""
    notes = []
    for trigger in context.get("triggers") or []:
        if not isinstance(trigger, dict):
            continue
        text = trigger.get("note") if trigger.get("kind") in ("wake_request", "news_alarm") else None
        if trigger.get("kind") == "check_in":
            text = (lookups.check_ins.get(trigger.get("check_in_id") or "") or {}).get("note")
        quoted = _quote(text, NOTE_QUOTE_CHARS)
        if quoted is not None and quoted not in notes:
            notes.append(quoted)
        if len(notes) == NOTES_MAX:
            break
    return notes


class _Lookups:
    """Records a page of wakes refers to, read once for the page: check-ins, focus selections, requests, alarms."""

    def __init__(self, reader: _Reader, rows: list) -> None:
        contexts = [_loads(row["context_json"]) for row in rows]
        outcomes = [_loads(row["outcome_json"]) for row in rows]
        triggers = [trigger for context in contexts for trigger in context.get("triggers") or []
                    if isinstance(trigger, dict)]
        self.check_ins = reader.check_ins([trigger.get("check_in_id") for trigger in triggers]
                                          + [outcome.get("check_in_id") for outcome in outcomes])
        self.focus_coins = reader.focus_coins([outcome.get("focus_id") for outcome in outcomes])
        self.requested = reader.trigger_departments(
            trigger_id for outcome in outcomes for trigger_id in outcome.get("requested_trigger_ids") or []
            if isinstance(trigger_id, str))
        self.tasks = reader.task_states(row["task_id"] for row in rows if row["status"] in OPEN_STATUSES)
        wanted = [trigger.get("alarm_id") for trigger in triggers if trigger.get("kind") == "alarm_fired"]
        self.conditions = reader.alarm_conditions(alarm for alarm in wanted if isinstance(alarm, str))


def _deliveries(context: dict) -> dict[str, dict]:
    result = {}
    for item in context.get("alarms") or []:
        if isinstance(item, dict) and isinstance(item.get("delivery_id"), str):
            result[item["delivery_id"]] = item.get("document") if isinstance(item.get("document"), dict) else {}
    return result


def _item(row, lookups: _Lookups) -> dict:
    context = _loads(row["context_json"])
    outcome = _loads(row["outcome_json"])
    triggers = [trigger for trigger in context.get("triggers") or [] if isinstance(trigger, dict)]
    reasons = _reasons(context)
    if not reasons or row["department"] not in DEPARTMENTS:
        raise ValueError("a wake without a readable reason or department")
    deliveries = _deliveries(context)
    events = [_event(trigger, context, deliveries, lookups.conditions, lookups.check_ins)
              for trigger in triggers[:EVENT_LINES_MAX]]
    allowance = _allowance(_loads(row["allowance_json"]))
    status = row["status"]
    created, closed = _parse(row["created_at"]), _parse(row["closed_at"])
    if created is None or (closed is None) != (status in OPEN_STATUSES):
        raise ValueError("a wake whose times can't be read")
    plans = [item for item in outcome.get("plans") or [] if isinstance(item, dict)]
    if status in ("failed", "obsolete"):
        tone = "attention"
    elif status == "completed" and any(item.get("status") == "installed" for item in plans):
        tone = "good"
    else:
        tone = "neutral"
    kinds = ", ".join(reason["kind"] for reason in reasons)
    return {
        "id": row["wake_id"], "cursor": int(row["sequence"]), "dept": row["department"], "origin": row["origin"],
        "purpose": row["purpose"], "status": status, "tone": tone, "at": iso_ms(created),
        "closed_at": iso_ms(closed) if closed else None, "headline": _headline(reasons, context),
        "reasons": reasons, "events": events, "more_events": max(0, len(triggers) - len(events)),
        "allowance": allowance,
        "result": _result(row, outcome, allowance, lookups.tasks.get(row["task_id"] or ""), lookups),
        "quote": _quote(outcome.get("summary")), "notes": _notes(context, lookups),
        "link": "/organization" if row["task_id"] else None,
        "technical": c.technical(f"{row['purpose']} wake ({status}): {kinds}"),
    }


# --- today -----------------------------------------------------------------------------------------------------


def _check_ins(reader: _Reader, day: TradingDay) -> list[dict]:
    """Each department's next check-in, as ``EventEngine.next_check_ins`` reads it (D123), with whether it is awake."""
    waiting = reader.waiting()
    awake: dict[str, Any] = {}
    for row in reader.open_wakes():
        awake.setdefault(row["department"], row)
    tasks = reader.task_states(row["task_id"] for row in awake.values())
    result = []
    for department in DEPARTMENTS:
        latest = reader.latest_check_in(department)
        named = latest is not None and latest["basis"] == "named"
        fired = named and reader.check_in_fired(latest["check_in_id"])
        due = _parse(latest["due_at"]) if named and not fired else None
        if due is None:
            at, basis = day.end, "day_start"
            words = W["check_in"]["never"] if latest is None else (
                W["check_in"]["came"] if fired else W["check_in"]["day_start"])
        else:
            at, basis = due, "named"
            words = W["check_in"]["named_due"] if due <= reader.now else W["check_in"]["named"]
        status = "due" if basis == "named" and at <= reader.now else "planned"
        text = c.says(words)
        if department in awake:
            row = awake[department]
            context = _loads(row["context_json"])
            reasons = _reasons(context)
            forms = W["check_in"]["awake" if tasks.get(row["task_id"] or "") in RUNNING_TASKS else "awake_waiting"]
            woken = _parse(row["created_at"])
            reason_words = c.plain(_headline(reasons, context)).lower() if reasons else W["reason"]["check_in"][0]
            text = c.says(forms[0], c.time(woken, "day_clock") if woken else None, forms[1], reason_words,
                          forms[2])
            status = "awake"
        keep_note = latest is not None and not fired
        result.append({
            "dept": department, "at": iso_ms(at), "basis": basis, "status": status, "text": text,
            "quote": _quote(latest["note"], NOTE_QUOTE_CHARS) if keep_note else None,
            "set_at": iso_ms(_parse(latest["created_at"])) if keep_note and _parse(latest["created_at"]) else None,
            "waiting": waiting.get(department, 0),
        })
    return result


def _step(name: str, record: dict | None, day: TradingDay, focus_failed_reason: list) -> dict:
    copy = W["step"][name]
    at = _parse(record["updated_at"]) if record else None
    if record is None:
        if name == "day_end_review":
            return {"step": name, "status": "not_yet", "at": iso_ms(day.end),
                    "text": c.says(copy["not_yet"][0], c.time(day.end, "day_clock"), copy["not_yet"][1])}
        return {"step": name, "status": "not_yet", "at": None, "text": c.says(copy["not_yet"])}
    status, detail = record["status"], record["detail"]
    code = c.technical(detail.get("reason")) if isinstance(detail.get("reason"), str) else None
    if name == "day_start":
        started = _parse(detail.get("started_at")) or _parse(record["created_at"])
        words = c.says(copy["done"][0], c.time(started, "day_clock") if started else None, copy["done"][1])
        late = detail.get("late_seconds")
        if isinstance(late, int) and late > LATE_AFTER_SECONDS:
            words = c.says(*words, copy["late"][0], c.duration(late), copy["late"][1])
        return {"step": name, "status": "done", "at": iso_ms(started) if started else None, "text": words}
    if name == "scan":
        scan = detail.get("scan") if isinstance(detail.get("scan"), dict) else {}
        shortlist = scan.get("shortlist") or []
        if scan.get("status") == "ok":
            words = c.says(_plural(copy["ok"], len(shortlist)))
            when = _parse(scan.get("as_of"))
            if scan.get("source") == "latest_recorded" and when is not None:
                words = c.says(*words, copy["saved"][0], c.time(when, "day_clock"), copy["saved"][1])
        elif scan.get("status") == "no_candidates":
            words = c.says(copy["no_candidates"])
        elif scan.get("status") == "none":
            words = c.says(copy["none"])
        else:
            words = c.says(c.text(copy["failed"], code=c.technical(scan.get("error"))))
        return {"step": name, "status": status, "at": iso_ms(at) if at else None, "text": words}
    if name == "day_plan" and status == "done":
        plans = [item for item in detail.get("plans") or [] if isinstance(item, dict)]
        placed = sum(1 for item in plans if item.get("status") in ("installed", "existing"))
        refused = sum(1 for item in plans if item.get("status") == "refused")
        late = sum(1 for item in plans if item.get("status") == "obsolete")
        parts = [_plural(W["plans_installed"], placed).strip()] if placed else []
        if refused:
            parts.append(_plural(W["plans_refused"], refused).strip())
        if late:
            parts.append(_plural(W["plans_obsolete"], late).strip())
        words = c.says(" ".join(parts) if parts else copy["none"])
        return {"step": name, "status": status, "at": iso_ms(at) if at else None, "text": words}
    if name == "focus" and status == "failed" and code:
        focus_failed_reason.append(code)
    return {"step": name, "status": status, "at": iso_ms(at) if at else None,
            "text": c.says(c.text(copy.get(status) or copy["not_yet"], code=code))}


def _focus(reader: _Reader, day: TradingDay, steps: dict[str, dict], reasons: list[str]) -> dict:
    copy = W["focus"]
    scan = (steps.get("scan") or {}).get("detail", {}).get("scan") or {}
    shortlist = [coin.get("symbol") for coin in scan.get("shortlist") or [] if isinstance(coin, dict)]
    shortlist = [symbol for symbol in shortlist if _symbol(symbol)][:SHORTLIST_MAX]
    empty = {"coins": [], "quote": None, "revision": None, "recorded_at": None, "shortlist": shortlist}
    row = reader.focus(day)
    if row is not None:
        document = _loads(row["document_json"])
        if _sha256(document) != row["document_sha256"]:
            return {"status": "failed", "text": c.says(copy["unreadable"]), **empty}
        coins = [{"symbol": coin["symbol"], "quote": _quote(coin.get("note"), NOTE_QUOTE_CHARS),
                  "evidence": len(coin.get("evidence_refs") or [])}
                 for coin in document.get("coins") or [] if isinstance(coin, dict) and _symbol(coin.get("symbol"))]
        coins = coins[:FOCUS_COINS_MAX]
        recorded = _parse(row["recorded_at"])
        revision = int(row["revision"])
        if coins:
            words = c.says(_plural(copy["named"], len(coins)))
        else:
            words = c.says(copy["none_named"])
        if revision > 1 and recorded is not None:
            words = c.says(*words, copy["revised"][0].format(revision=revision), c.time(recorded, "day_clock"),
                           copy["revised"][1])
        return {"status": "named" if coins else "none_named", "text": words, "coins": coins,
                "quote": _quote(document.get("summary")), "revision": revision,
                "recorded_at": iso_ms(recorded) if recorded else None, "shortlist": shortlist}
    step = steps.get("focus")
    status = step["status"] if step else None
    if status == "failed":
        return {"status": "failed", "text": c.says(c.text(copy["failed"], code=reasons[0] if reasons else None)),
                **empty}
    if status == "skipped":
        return {"status": "skipped", "text": c.says(copy["skipped"]), **empty}
    return {"status": "not_yet", "text": c.says(W["step"]["focus"]["started"] if status == "started"
                                                 else copy["not_yet"]), **empty}


def _alarms(reader: _Reader) -> dict:
    copy = W["alarms"]
    total, rows = reader.armed_alarms()
    items = []
    for row in rows:
        symbol = _symbol(row["symbol"])
        armed = _parse(row["armed_at"])
        if symbol is None or row["department"] not in DEPARTMENTS:
            continue
        items.append({"id": row["alarm_id"], "dept": row["department"], "symbol": symbol,
                      "text": c.says(alarm_words(_loads(row["condition_json"]))),
                      "armed_at": iso_ms(armed) if armed else None, "quote": _quote(row["note"], NOTE_QUOTE_CHARS),
                      "in_day_plan": bool(row["day_plan_ref"])})
    fired = reader.fired_waiting()
    waiting = [{"dept": department, "count": fired[department]} for department in DEPARTMENTS
               if fired.get(department)]
    words = [copy["none"] if total == 0 else _plural(copy["set"], total)]
    for item in waiting:
        words.append(_plural(copy["waiting"], item["count"], who=_who(item["dept"])))
    return {"total": max(total, len(items)), "text": c.says("".join(words)), "items": items, "waiting": waiting}


def _counts(reader: _Reader, day: TradingDay) -> dict:
    copy = W["counts"]
    today, week = reader.counts(day.start), reader.counts(min(day.start, reader.now - WEEK))
    departments = [{"dept": department, "today": today.get(department, 0), "week": week.get(department, 0)}
                   for department in DEPARTMENTS]
    total_today = sum(item["today"] for item in departments)
    total_week = sum(item["week"] for item in departments)
    reasons = reader.reasons(day.start)
    words = (_plural(copy["today"], total_today) if total_today else copy["today_none"]) + \
        copy["week"].format(n=total_week)
    return {"today": total_today, "week": total_week, "text": c.says(words), "departments": departments,
            "reasons_today": [{"kind": kind, "count": reasons[kind]} for kind in TRIGGER_KINDS if reasons.get(kind)]}


def _day(reader: _Reader) -> dict:
    day = reader.day()
    records = reader.steps(day)
    failed_focus: list = []
    steps = [_step(name, records.get(name), day, failed_focus) for name in CYCLE_STEPS]
    return {"start": iso_ms(day.start), "end": iso_ms(day.end), "steps": steps, "check_ins": _check_ins(reader, day),
            "focus": _focus(reader, day, records, failed_focus), "alarms": _alarms(reader),
            "counts": _counts(reader, day)}


# --- the document and the route ---------------------------------------------------------------------------------


def wake_log(runtime, *, before: int | None = None, limit: int = PAGE_DEFAULT) -> dict:
    """One page of the wake log, newest first; the first page (no ``before``) also carries today's block."""
    if (before is not None and (isinstance(before, bool) or not isinstance(before, int) or before < 1)) or \
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_MAX:
        raise ValueError(f"'before' is a positive wake cursor and 'limit' is 1 to {PAGE_MAX}")
    with runtime.database.snapshot():
        now = runtime.clock.now()
        reader = _Reader(runtime.database, runtime.portfolio_id, now)
        base = {"schema": SCHEMA, "as_of": iso_ms(now)}
        if not reader.available:
            return {**base, "available": False, "status_text": c.says(W["unavailable"]), "day": None, "items": [],
                    "total": 0, "next_before": None}
        rows = reader.page(before, limit)
        lookups = _Lookups(reader, rows)
        items, unreadable = [], 0
        for row in rows:
            try:
                items.append(_item(row, lookups))
            except (ValueError, KeyError, TypeError):
                unreadable += 1  # never invented: the record is left out and counted
        total = reader.total()
        notes = []
        if not total:
            notes.append(W["none_yet"])
        if unreadable:
            notes.append(_plural(W["unreadable"], unreadable))
        next_before = rows[-1]["sequence"] if rows and len(rows) == limit and reader.older(rows[-1]["sequence"]) \
            else None
        return {**base, "available": True, "status_text": c.says(" ".join(notes)) if notes else None,
                "day": _day(reader) if before is None else None, "items": items, "total": total,
                "next_before": int(next_before) if next_before is not None else None}


def register(app, runtime, identity) -> None:
    """Add ``GET /api/v1/hq/wakes`` (any signed-in role; reads only)."""

    @app.get("/api/v1/hq/wakes")
    def hq_wakes(request: Request, before: WakesBefore = None, limit: WakesLimit = PAGE_DEFAULT):
        identity(request)
        try:
            return redact(wake_log(runtime, before=before, limit=limit))
        except ValueError:
            raise HTTPException(422, "invalid wake log query") from None


__all__ = ["alarm_words", "register", "wake_log"]
