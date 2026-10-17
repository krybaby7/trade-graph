"""HQ price charts (T41, D197): one coin's candles, chosen indicators, alarm lines, orders, safety exits and fills.

``chart(runtime, symbol=None, interval=None)`` builds one ``trade-graph.hq.chart.v1`` document
(``api/hq_chart_contract.py``) from saved records only, in one read snapshot of each database:

- **coins**: the coins of today's day plans, coins held, coins with an armed alarm, the newest live scanner shortlist
  (if it is under a day old and ranked something) and the market weather's BTC/USD and ETH/USD;
- **candles**: the candle file (``runtime.chart_candles``, a ``CandleStore`` over ``ReadOnlyMarketData``), point in
  time as of now through ``CandleStore.window``; a missing slot stays missing, nothing is carried forward;
- **indicators**: the coin's day-plan indicators on this interval and the ones its armed indicator alarms name,
  computed by the T27 engine over the same candles (D118: no formula here; a gap withholds an indicator, as for the
  Trader); ``PLACEMENT`` only says where a menu entry is drawn;
- **lines**: armed alarms (``price_alarms``) and open orders (``order_intents``): day-plan resting entries, safety
  exits (``day_plan_entries`` / ``day_plan_exits``) and any other open order of the coin;
- **markers**: fills of the coin (``fills``) and alarm fires (``price_alarm_events``) inside the window.

Reading a chart starts nothing, sends nothing and writes nothing (D107, D110). A table a database does not have yet
(an older schema) reads as no records. The page draws the document as it is; every word comes from here.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import HTTPException, Query, Request

from trade_graph.adapters.persistence.market_data_readonly import MarketDataUnavailable, ReadOnlyMarketData
from trade_graph.api import hq_copy as c
from trade_graph.api.hq_chart_contract import (
    CHART_SOURCE,
    DEFAULT_INTERVAL,
    INTERVALS,
    MAX_COINS,
    MAX_MARKERS,
    REFERENCE_SYMBOLS,
    SCHEMA,
    VALUE_DIGITS,
    WINDOW_SLOTS,
)
from trade_graph.api.hq_contract import iso_ms
from trade_graph.api.hq_facts import OPEN_ORDER_STATES, UNCLEAR_ORDER_STATES
from trade_graph.api.security import redact
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.engine import compute_indicator
from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.candles import align_close_time, from_unix, to_unix
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal

# Where each menu entry is drawn: on the price pane (its value is a price level) or on a pane of its own. Display
# classification only (D136's reading of D118); tests/integration/test_hq_chart.py keeps it in step with the menu.
PLACEMENT: Final[dict[str, str]] = {
    "SMA": "price", "EMA": "price", "WMA": "price", "DEMA": "price", "BBANDS": "price", "SAR": "price",
    "MIDPRICE": "price", "HIGHEST_HIGH": "price", "LOWEST_LOW": "price",
    "RSI": "own", "MACD": "own", "STOCH": "own", "CCI": "own", "MFI": "own", "WILLR": "own", "ROC": "own",
    "ROCP": "own", "MOM": "own", "ADX": "own", "PLUS_DI": "own", "MINUS_DI": "own", "AROON": "own", "ATR": "own",
    "NATR": "own", "OBV": "own", "STDDEV": "own",
}
# A busy synthetic chart (8 indicators with 14 outputs, 59 lines, 300 candles) measures about 69 KB compact; HQ's
# snapshot budget is reused as this endpoint's own ceiling (tests/integration/test_hq_chart.py).
PAYLOAD_BUDGET_BYTES: Final = 80 * 1024
# The newest candle is stored a few seconds after it closes. Until it arrives the chart ends at the one before it
# rather than at a hole; a feed more than this many candles behind shows its empty slots at the right edge.
FEED_LAG_SLOTS: Final = 2
SHORTLIST_MAX_AGE: Final = timedelta(hours=24)
FILL_SCAN_LIMIT: Final = 5000
_SYMBOL = re.compile(SYMBOL_PATTERN)
INTERVAL_WORDS: Final = {1: "1-minute", 5: "5-minute", 15: "15-minute", 60: "1-hour"}
RELATION_WORDS: Final = {"above": "above", "below": "below", "crosses_above": "crosses above",
                         "crosses_below": "crosses below"}
QUOTE_WORDS: Final = {"mid": "mid price", "bid": "bid", "ask": "ask", "last": "last trade price"}
CANDLE_WORDS: Final = {"close": "close", "high": "high", "low": "low"}
NO_HISTORY: Final = {
    "not_configured": "Price history isn't connected to this HQ, so there are no candles to draw.",
    "missing": "There is no price history file yet, so there are no candles to draw.",
    "unreadable": "The price history file can't be read (it must be a private candle file), so there are no "
                  "candles to draw.",
    "outdated": "The price history file needs its upgrade by the price collector first, so there are no candles "
                "to draw.",
}
NOT_LISTED: Final = "That coin isn't on HQ's chart list now."
SOURCE_NOTES: Final = {"synthetic": "These are test candles (synthetic), not market prices."}
ChartInterval = Annotated[int | None, Query(ge=1, le=1440)]
ChartSymbol = Annotated[str | None, Query(min_length=3, max_length=24)]


# --- the candle file ------------------------------------------------------------------------------------------


class ChartCandleFile:
    """The dashboard's handle on the candle file (D199): opened read-only on first use, and tried again on the next
    chart after a refusal, so a file the collector creates later is picked up without restarting HQ."""

    def __init__(self, path: Path | str | None) -> None:
        self.path = None if path is None else Path(path)
        self._store: CandleStore | None = None
        self._lock = threading.Lock()

    def open(self) -> tuple[CandleStore | None, str]:
        with self._lock:
            if self._store is not None:
                return self._store, "connected"
            if self.path is None:
                return None, "not_configured"
            try:
                self._store = CandleStore(ReadOnlyMarketData(self.path))
            except MarketDataUnavailable as error:
                return None, error.reason
            return self._store, "connected"

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                self._store.database.close()
                self._store = None


def _candle_store(runtime) -> tuple[CandleStore | None, str]:
    value = getattr(runtime, "chart_candles", None)
    if isinstance(value, ChartCandleFile):
        return value.open()
    if value is None:
        return None, getattr(runtime, "chart_candles_status", None) or "not_configured"
    return value, "connected"


# --- small helpers --------------------------------------------------------------------------------------------


def _tables(database) -> set[str]:
    return {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _text(value: Any) -> str | None:
    number = _decimal(value)
    return None if number is None else canonical_decimal(number)


def _words(value: Any) -> str:
    """A level, price or quantity for a sentence: exact, with thousands separators."""
    number = _decimal(value)
    return "an unknown amount" if number is None else format(Decimal(canonical_decimal(number)), ",f")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _department(name: Any) -> str:
    try:
        return c.dept_name(str(name))
    except KeyError:
        return "A department"


def _base(symbol: str) -> str:
    return symbol.split("/", 1)[0]


def _parameter_words(value: int | float) -> str:
    return str(value) if isinstance(value, int) else format(value, "g")


def indicator_key(name: str, interval: int, parameters: dict[str, int | float]) -> str:
    settings = ",".join(f"{key}={parameters[key]}" for key in sorted(parameters))
    return f"{name}|{interval}|{settings}"


def indicator_label(name: str, parameters: dict[str, int | float]) -> str:
    spec = get_indicator(name)
    values = [_parameter_words(parameters[item.name]) for item in spec.parameters if item.name in parameters]
    return f"{name} {', '.join(values)}" if values else name


# --- what the state database says -----------------------------------------------------------------------------


class _Records:
    """The state database's chart facts, read once inside the request's snapshot."""

    def __init__(self, runtime, now: datetime) -> None:
        self.runtime = runtime
        self.database = runtime.database
        self.pid = runtime.portfolio_id
        self.now = now
        self.tables = _tables(self.database)

    def has(self, *names: str) -> bool:
        return all(name in self.tables for name in names)

    def plans(self) -> list[dict]:
        """The latest plan of each coin whose trading day has not ended (``DayPlans.active``)."""
        if not self.has("day_plans"):
            return []
        rows = self.database.execute(
            """SELECT plan_id, symbol, document_json FROM day_plans p WHERE portfolio_id = ? AND trading_day_end > ?
               AND NOT EXISTS (SELECT 1 FROM day_plans n WHERE n.supersedes_plan_id = p.plan_id) ORDER BY sequence""",
            (self.pid, utc_iso(self.now))).fetchall()
        return [{"plan_id": row["plan_id"], "symbol": row["symbol"], "document": json.loads(row["document_json"])}
                for row in rows]

    def held(self) -> list[str]:
        ledger = getattr(self.runtime, "ledger", None)
        if ledger is None:
            return []
        try:
            books = ledger.books(self.pid)
        except (TradeGraphError, ValueError, KeyError, TypeError):
            return []  # never invented: a coin is listed as held only when the books say so
        held = []
        for lot in books.lots:
            if lot.open_quantity() > 0 and f"{lot.asset}/USD" not in held:
                held.append(f"{lot.asset}/USD")
        return held

    def alarms(self, *, symbol: str | None = None, armed_only: bool = True) -> list[dict]:
        if not self.has("price_alarms", "price_alarm_watch"):
            return []
        clauses = ["(a.portfolio_id IS NULL OR a.portfolio_id = ?)"]
        values: list[Any] = [self.pid]
        if armed_only:
            clauses.append("w.status = 'armed'")
        if symbol is not None:
            clauses.append("a.symbol = ?")
            values.append(symbol)
        rows = self.database.execute(
            f"""SELECT a.alarm_id, a.department, a.symbol, a.condition_json, w.status FROM price_alarms a
                JOIN price_alarm_watch w USING (alarm_id) WHERE {' AND '.join(clauses)} ORDER BY a.sequence""",
            tuple(values)).fetchall()
        return [{"alarm_id": row["alarm_id"], "department": row["department"], "symbol": row["symbol"],
                 "condition": json.loads(row["condition_json"]), "status": row["status"]} for row in rows]

    def fires(self, symbol: str, since: datetime) -> list[dict]:
        if not self.has("price_alarms", "price_alarm_events"):
            return []
        rows = self.database.execute(
            """SELECT e.at, a.department, a.condition_json FROM price_alarm_events e JOIN price_alarms a USING
               (alarm_id) WHERE a.symbol = ? AND (a.portfolio_id IS NULL OR a.portfolio_id = ?)
               AND e.action = 'fired' ORDER BY e.sequence DESC LIMIT ?""",
            (symbol, self.pid, FILL_SCAN_LIMIT)).fetchall()
        fires = []
        for row in rows:
            at = _parse(row["at"])
            if at is not None and since <= at <= self.now:
                fires.append({"at": at, "department": row["department"],
                              "condition": json.loads(row["condition_json"])})
        return fires

    def shortlist(self) -> list[str]:
        """The newest live scan's shortlist, if that scan is under a day old and ranked something."""
        if not self.has("coin_scans", "coin_shortlist_entries"):
            return []
        latest = self.database.execute(
            """SELECT sequence, status, as_of FROM coin_scans WHERE mode = 'live' AND as_of <= ?
               ORDER BY as_of DESC, sequence DESC LIMIT 1""", (utc_iso(self.now),)).fetchone()
        when = _parse(latest["as_of"]) if latest else None
        if latest is None or latest["status"] != "ok" or when is None or when < self.now - SHORTLIST_MAX_AGE:
            return []
        return [row["symbol"] for row in self.database.execute(
            "SELECT symbol FROM coin_shortlist_entries WHERE scan_sequence = ? ORDER BY position",
            (latest["sequence"],)).fetchall()]

    def orders(self, symbol: str) -> list[dict]:
        marks = ",".join("?" for _ in OPEN_ORDER_STATES)
        rows = self.database.execute(
            f"""SELECT intent_id, state, payload_json FROM order_intents WHERE portfolio_id = ? AND symbol = ?
                AND state IN ({marks}) ORDER BY created_at, rowid""",
            (self.pid, symbol, *sorted(OPEN_ORDER_STATES))).fetchall()
        return [{"intent_id": row["intent_id"], "state": row["state"], "payload": json.loads(row["payload_json"])}
                for row in rows]

    def roles(self, intent_ids: list[str]) -> tuple[dict[str, dict], set[str]]:
        """(day-plan entry rows by intent, safety-exit intents) among ``intent_ids``."""
        if not intent_ids or not self.has("day_plan_entries", "day_plan_exits"):
            return {}, set()
        marks = ",".join("?" for _ in intent_ids)
        entries = {row["intent_id"]: dict(row) for row in self.database.execute(
            f"SELECT intent_id, quantity, limit_price, stop_price, take_profit_price FROM day_plan_entries "
            f"WHERE intent_id IN ({marks})", tuple(intent_ids)).fetchall()}
        exits = {row["intent_id"] for row in self.database.execute(
            f"SELECT intent_id FROM day_plan_exits WHERE intent_id IN ({marks})", tuple(intent_ids)).fetchall()}
        return entries, exits

    def fills(self, symbol: str, since: datetime) -> list[dict]:
        rows = self.database.execute(
            """SELECT intent_id, document_json FROM fills WHERE portfolio_id = ?
               AND json_extract(document_json, '$.symbol') = ? ORDER BY rowid DESC LIMIT ?""",
            (self.pid, symbol, FILL_SCAN_LIMIT)).fetchall()
        fills = []
        for row in rows:
            document = json.loads(row["document_json"])
            at = _parse(document.get("filled_at_utc"))
            if at is not None and since <= at <= self.now:
                fills.append({"intent_id": row["intent_id"], "at": at, "document": document})
        return fills


def coins(records: _Records, plans: list[dict]) -> list[dict]:
    reasons: dict[str, list[str]] = {}

    def add(symbol: Any, reason: str) -> None:
        if isinstance(symbol, str) and _SYMBOL.match(symbol):
            listed = reasons.setdefault(symbol, [])
            if reason not in listed:
                listed.append(reason)

    for item in plans:
        add(item["symbol"], "day_plan")
    for symbol in records.held():
        add(symbol, "held")
    for alarm in records.alarms():
        add(alarm["symbol"], "alarm")
    for symbol in records.shortlist():
        add(symbol, "shortlist")
    for symbol in REFERENCE_SYMBOLS:
        add(symbol, "reference")
    order = ("day_plan", "held", "alarm", "shortlist", "reference")
    listed = [{"symbol": symbol, "reasons": sorted(found, key=order.index)} for symbol, found in reasons.items()]
    return listed[:MAX_COINS]


# --- lines, indicators and markers ----------------------------------------------------------------------------


def _alarm_words(condition: dict) -> str:
    relation = RELATION_WORDS.get(condition.get("relation"), str(condition.get("relation")))
    level = _words(condition.get("level"))
    kind = condition.get("kind")
    if kind == "quote_price":
        return f"{QUOTE_WORDS.get(condition.get('price', 'mid'), 'price')} {relation} {level}"
    interval = INTERVAL_WORDS.get(condition.get("interval_minutes"), "candle")
    if kind == "candle_price":
        return f"{interval} candle {CANDLE_WORDS.get(condition.get('price', 'close'), 'close')} {relation} {level}"
    name = str(condition.get("indicator"))
    try:
        label = indicator_label(name, dict(condition.get("parameters") or {}))
    except TradeGraphError:
        label = name
    output = condition.get("output")
    spec_outputs = _outputs(name)
    named = f" {str(output).replace('_', ' ')}" if output and len(spec_outputs) > 1 else ""
    return f"{label}{named} ({interval} candles) {relation} {level}"


def _outputs(name: str) -> tuple[str, ...]:
    try:
        return get_indicator(name).output_names
    except TradeGraphError:
        return ()


def _alarm_indicator(condition: dict) -> tuple[str, str, int, dict] | None:
    """(key, name, interval, resolved settings) of an indicator alarm on the menu, else None."""
    name = condition.get("indicator")
    interval = condition.get("interval_minutes")
    if condition.get("kind") != "indicator" or interval not in INTERVALS:
        return None
    try:
        settings = get_indicator(str(name)).resolve_parameters(dict(condition.get("parameters") or {}))
    except TradeGraphError:
        return None
    return indicator_key(str(name), interval, settings), str(name), interval, settings


def _chosen(plans: list[dict], alarms: list[dict], symbol: str, interval: int) -> tuple[list[dict], list[str]]:
    """The indicators to draw on this interval, and sentences for the ones watched on another interval."""
    chosen: dict[str, dict] = {}
    elsewhere: list[str] = []
    plan = next((item for item in reversed(plans) if item["symbol"] == symbol), None)
    for item in (plan["document"].get("indicators") or []) if plan else []:
        name, on = item.get("indicator"), item.get("interval_minutes")
        try:
            settings = get_indicator(str(name)).resolve_parameters(
                {entry["name"]: entry["value"] for entry in item.get("parameters") or []})
        except (TradeGraphError, KeyError, TypeError):
            continue
        if on != interval:
            if on in INTERVALS:
                label = indicator_label(str(name), settings)
                elsewhere.append(f"{label} ({get_indicator(str(name)).title}) is watched on the "
                                 f"{INTERVAL_WORDS[on]} chart.")
            continue
        key = indicator_key(str(name), interval, settings)
        chosen.setdefault(key, {"name": str(name), "settings": settings, "chosen_by": []})
        if "day_plan" not in chosen[key]["chosen_by"]:
            chosen[key]["chosen_by"].append("day_plan")
    for alarm in alarms:
        found = _alarm_indicator(alarm["condition"])
        if found is None or found[2] != interval:
            continue
        key, name, _, settings = found
        chosen.setdefault(key, {"name": name, "settings": settings, "chosen_by": []})
        if "alarm" not in chosen[key]["chosen_by"]:
            chosen[key]["chosen_by"].append("alarm")
    return [{"key": key, **value} for key, value in chosen.items()], list(dict.fromkeys(elsewhere))


def _indicator(store, item: dict, *, symbol: str, interval: int, source: str, end_close: datetime,
               now: datetime) -> dict:
    name, settings = item["name"], item["settings"]
    spec = get_indicator(name)
    label = indicator_label(name, settings)
    document = {"key": item["key"], "indicator": name, "title": spec.title, "label": label, "parameters": settings,
                "interval_minutes": interval, "pane": PLACEMENT.get(name, "own"), "status": "unavailable",
                "status_text": None, "chosen_by": item["chosen_by"], "outputs": []}
    try:
        result = compute_indicator(store, name, settings, symbol=symbol, interval_minutes=interval, source=source,
                                   end_close_time=end_close, as_of=now, count=WINDOW_SLOTS)
    except TradeGraphError:
        document["status_text"] = f"{label} couldn't be worked out from the saved candles."
        return document
    document["status"] = result.status
    if result.status == "ready":
        names = spec.output_names
        document["outputs"] = [{
            "name": output, "label": label if len(names) == 1 else f"{label} · {output.replace('_', ' ')}",
            "style": "histogram" if output == "histogram" else "line",
            "values": [float(f"{value:.{VALUE_DIGITS}g}") for value in result.values[output]],
        } for output in names]
    elif result.status == "warming_up":
        document["status_text"] = (f"{label} isn't drawn yet: this coin's saved history is too short for it (it needs "
                                   f"{result.required_candles} candles, {result.available_candles} are saved).")
    else:
        missing = len(result.missing_close_times_utc)
        verb = "is" if missing == 1 else "are"
        document["status_text"] = (f"{label} isn't drawn: {_plural(missing, 'candle', 'candles')} it needs {verb} "
                                   "missing. Nothing is filled in.")
    return document


def _alarm_lines(alarms: list[dict], drawn: dict[str, dict], interval: int) -> tuple[list[dict], list[str]]:
    lines, elsewhere = [], []
    for alarm in alarms:
        condition = alarm["condition"]
        level = _text(condition.get("level"))
        if level is None:
            continue
        text = f"{_department(alarm['department'])}'s alarm: {_alarm_words(condition)}"
        pane = "price"
        found = _alarm_indicator(condition)
        if condition.get("kind") == "indicator":
            name = str(condition.get("indicator"))
            if found is None or PLACEMENT.get(name) != "price":
                key = found[0] if found else None
                if key is not None and found[2] == interval and drawn.get(key, {}).get("status") == "ready":
                    pane = key
                elif found is not None and found[2] == interval:
                    elsewhere.append(f"{text} (not drawn while the indicator has no values on this chart).")
                    continue
                else:
                    where = INTERVAL_WORDS.get(condition.get("interval_minutes"), "its own")
                    elsewhere.append(f"{text} (on the {where} chart).")
                    continue
        lines.append({"kind": "alarm", "price": level, "title": "Alarm", "text": text, "pane": pane,
                      "state": "armed"})
    return lines, elsewhere


def _order_lines(records: _Records, symbol: str) -> list[dict]:
    orders = records.orders(symbol)
    entries, exits = records.roles([order["intent_id"] for order in orders])
    base = _base(symbol)
    lines = []
    for order in orders:
        payload = order["payload"]
        unclear = order["state"] in UNCLEAR_ORDER_STATES
        state = "unclear" if unclear else "open"
        tail = " (its status is being checked)" if unclear else (
            ", partly filled" if order["state"] == "PARTIALLY_FILLED" else "")
        quantity = _words(payload.get("quantity"))
        side, kind = payload.get("side"), payload.get("order_type")
        intent = order["intent_id"]
        if intent in entries:
            row = entries[intent]
            after = f"After a fill: stop {_words(row['stop_price'])}"
            if row.get("take_profit_price"):
                after += f", target {_words(row['take_profit_price'])}"
            line = {"kind": "entry", "price": _text(row["limit_price"]), "title": "Buy",
                    "text": f"Resting buy of {quantity} {base} at {_words(row['limit_price'])}{tail} (day plan). "
                            f"{after}."}
            lines.append({**line, "pane": "price", "state": state})
            continue
        if side == "sell" and kind == "stop":
            target = _text(payload.get("take_profit_price"))
            who = "Safety exit" if intent in exits else "Stop order"
            stop_words = _words(payload.get("stop_price"))
            either = f", or target at {_words(target)}, whichever comes first" if target else ""
            lines.append({"kind": "stop", "price": _text(payload.get("stop_price")), "title": "Stop",
                          "text": f"{who} for {quantity} {base}: stop at {stop_words}{either}{tail}.",
                          "pane": "price", "state": state})
            if target:
                lines.append({"kind": "take_profit", "price": target, "title": "Target",
                              "text": f"{who} for {quantity} {base}: target at {_words(target)}, or stop at "
                                      f"{stop_words}{tail}.", "pane": "price", "state": state})
            continue
        price = payload.get("limit_price") if kind == "limit" else payload.get("stop_price")
        if _text(price) is None:
            continue  # a market order has no level to draw
        lines.append({"kind": "order", "price": _text(price), "title": "Order",
                      "text": f"Open {side} {kind} order for {quantity} {base} at {_words(price)}{tail}.",
                      "pane": "price", "state": state})
    return [line for line in lines if line["price"] is not None]


def _markers(records: _Records, symbol: str, since: datetime, step: int) -> tuple[list, int]:
    fills = records.fills(symbol, since)
    entries, exits = records.roles(sorted({item["intent_id"] for item in fills if item["intent_id"]}))
    base = _base(symbol)
    markers = []
    for item in fills:
        document = item["document"]
        price = _text(document.get("price"))
        if price is None:
            continue
        side = document.get("side")
        role = " (day plan buy)" if item["intent_id"] in entries else " (safety exit)" \
            if item["intent_id"] in exits else ""
        verb = "Bought" if side == "buy" else "Sold"
        markers.append({"kind": "buy" if side == "buy" else "sell", "t": _slot(item["at"], step),
                        "at": iso_ms(item["at"]), "price": price,
                        "text": f"{verb} {_words(document.get('quantity'))} {base} at {_words(price)}{role}"})
    for fire in records.fires(symbol, since):
        condition = fire["condition"]
        on_price = condition.get("kind") != "indicator" or PLACEMENT.get(str(condition.get("indicator"))) == "price"
        markers.append({"kind": "alarm", "t": _slot(fire["at"], step), "at": iso_ms(fire["at"]),
                        "price": _text(condition.get("level")) if on_price else None,
                        "text": f"{_department(fire['department'])}'s alarm went off: {_alarm_words(condition)}"})
    markers.sort(key=lambda marker: (marker["t"], marker["at"]))
    total = len(markers)
    return markers[-MAX_MARKERS:], total


def _slot(moment: datetime, step: int) -> int:
    seconds = to_unix(moment.replace(microsecond=0))
    return seconds - seconds % step


def _precision(candles: list[list], lines: list[dict]) -> int:
    texts = [text for item in candles for text in item[1:5]] or [line["price"] for line in lines]
    places = [len(text.split(".", 1)[1]) for text in texts if "." in text]
    return min(max(places, default=2 if not texts else 0), 12)


# --- the document ---------------------------------------------------------------------------------------------


def _empty(now: datetime, listed: list[dict], source: str, *, status: str, text: str, symbol: str | None,
           interval: int | None, lines: list[dict], elsewhere: list[str]) -> dict:
    return {"schema": SCHEMA, "as_of": iso_ms(now), "status": status, "status_text": text, "coins": listed,
            "symbol": symbol, "interval_minutes": interval, "intervals": list(INTERVALS), "source": source,
            "price_precision": None, "window": None, "newest_close": None, "candles": [], "missing": [],
            "indicators": [], "lines": lines, "markers": [], "elsewhere": elsewhere, "notes": []}


def _default_interval(plans: list[dict], alarms: list[dict], symbol: str) -> int:
    plan = next((item for item in reversed(plans) if item["symbol"] == symbol), None)
    for item in (plan["document"].get("indicators") or []) if plan else []:
        if item.get("interval_minutes") in INTERVALS:
            return item["interval_minutes"]
    for alarm in alarms:
        if alarm["condition"].get("interval_minutes") in INTERVALS:
            return alarm["condition"]["interval_minutes"]
    return DEFAULT_INTERVAL


def chart(runtime, *, symbol: str | None = None, interval: int | None = None, source: str = CHART_SOURCE) -> dict:
    """One chart document. ``symbol`` defaults to the first listed coin, ``interval`` to the coin's plan."""
    if symbol is not None and (not isinstance(symbol, str) or not _SYMBOL.match(symbol)):
        raise ValueError("a symbol such as BTC/USD is required")
    if interval is not None and interval not in INTERVALS:
        raise ValueError(f"the interval is one of {INTERVALS}")
    with runtime.database.snapshot():
        now = runtime.clock.now()
        records = _Records(runtime, now)
        plans = records.plans()
        listed = coins(records, plans)
        chosen = symbol or listed[0]["symbol"]
        if chosen not in {coin["symbol"] for coin in listed}:
            return _empty(now, listed, source, status="coin_not_listed", text=NOT_LISTED, symbol=None,
                          interval=None, lines=[], elsewhere=[])
        alarms = records.alarms(symbol=chosen)
        interval = interval or _default_interval(plans, alarms, chosen)
        orders = _order_lines(records, chosen)
        store, status = _candle_store(runtime)
        if store is None:
            alarm_lines, elsewhere = _alarm_lines(alarms, {}, interval)
            return _empty(now, listed, source, status="no_price_history",
                          text=NO_HISTORY.get(status, NO_HISTORY["not_configured"]), symbol=chosen, interval=interval,
                          lines=alarm_lines + orders, elsewhere=elsewhere)
        snapshot = getattr(store.database, "snapshot", None)
        with snapshot() if callable(snapshot) else nullcontext():
            return _drawn(records, store, plans, alarms, orders, listed, symbol=chosen, interval=interval,
                          source=source)


def _drawn(records: _Records, store, plans, alarms, orders, listed, *, symbol: str, interval: int,
           source: str) -> dict:
    now = records.now
    step = interval * 60
    forming = align_close_time(now, interval)
    probe = store.window(symbol, interval, source, end_close_time=forming, count=WINDOW_SLOTS + FEED_LAG_SLOTS,
                         as_of=now, mode="point_in_time")
    present = [index for index, slot in enumerate(probe.slots) if slot.stored is not None]
    last = len(probe.slots) - 1
    end = present[-1] if present and present[-1] >= last - FEED_LAG_SLOTS else last
    slots = probe.slots[end - WINDOW_SLOTS + 1:end + 1]
    end_close = slots[-1].close_time_utc
    first_open = to_unix(slots[0].close_time_utc) - step
    candles, missing = [], []
    for slot in slots:
        opened = to_unix(slot.close_time_utc) - step
        if slot.stored is None:
            missing.append(opened)
            continue
        item = slot.stored.candle
        candles.append([opened, canonical_decimal(item.open), canonical_decimal(item.high),
                        canonical_decimal(item.low), canonical_decimal(item.close), canonical_decimal(item.volume)])
    chosen, elsewhere = _chosen(plans, alarms, symbol, interval)
    indicators = [_indicator(store, item, symbol=symbol, interval=interval, source=source, end_close=end_close,
                             now=now) for item in chosen]
    drawn = {item["key"]: item for item in indicators}
    alarm_lines, alarm_elsewhere = _alarm_lines(alarms, drawn, interval)
    lines = alarm_lines + orders
    markers, total = _markers(records, symbol, from_unix(first_open), step)
    notes = []
    if not candles:
        notes.append(f"No {INTERVAL_WORDS[interval]} candles of {symbol} are saved for this window yet.")
    elif missing:
        verb = "is" if len(missing) == 1 else "are"
        notes.append(f"{_plural(len(missing), 'candle', 'candles')} {verb} missing in this window. Gaps stay empty; "
                     "nothing is filled in.")
    if total > len(markers):
        notes.append(f"Only the newest {len(markers)} of {total} fills and alarm fires are marked.")
    if source in SOURCE_NOTES:
        notes.append(SOURCE_NOTES[source])
    newest = probe.slots[present[-1]].close_time_utc if present else None
    return {
        "schema": SCHEMA, "as_of": iso_ms(now), "status": "ok", "status_text": None, "coins": listed,
        "symbol": symbol, "interval_minutes": interval, "intervals": list(INTERVALS), "source": source,
        "price_precision": _precision(candles, lines),
        "window": {"first_open": first_open, "step_seconds": step, "slots": len(slots),
                   "now_open": to_unix(forming)},
        "newest_close": iso_ms(newest) if newest is not None else None,
        "candles": candles, "missing": missing, "indicators": indicators, "lines": lines, "markers": markers,
        "elsewhere": elsewhere + alarm_elsewhere, "notes": notes,
    }


def register(app, runtime, identity) -> None:
    """Add ``GET /api/v1/hq/chart`` (any signed-in role; reads only)."""

    @app.get("/api/v1/hq/chart")
    def hq_chart(request: Request, symbol: ChartSymbol = None, interval: ChartInterval = None):
        identity(request)
        try:
            return redact(chart(runtime, symbol=symbol, interval=interval))
        except ValueError:
            raise HTTPException(422, "invalid chart query") from None


__all__ = ["FEED_LAG_SLOTS", "PAYLOAD_BUDGET_BYTES", "PLACEMENT", "ChartCandleFile", "chart", "coins",
           "indicator_key", "indicator_label", "register"]
