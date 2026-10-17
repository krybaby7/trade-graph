"""HQ's today's-trading block (W8-C, D250-D253): today's loss against the owner's daily loss limit, the paper
scorecard and the price collector, as ``trade-graph.hq.v1`` ``trading``.

- **Today's loss** is W3-A's own reading, ``MoneyLimitGuard.status(portfolio, policy)`` with ``record=False`` and its
  ``LossStatus.document()``: the day-start value, the current value with fees and coins held at their marks, the
  loss, the limit amount and whether the day's stop is recorded. Reading never records a day-start value or a stop
  (only the paper service's management step does) and never decides anything.
- **The scorecard** is W5-C's ``compute_scorecard`` for the paper portfolio from its opening deposit to now (D200's
  default period), shown from its ``document()``: the result after every fee against keeping the cash and holding
  Bitcoin, the trade figures, euros, the real AI spending of the same days (never paid by the paper result), the
  owner's test rule read as information and the statement that the three gates are separate (D204).
- **The price collector** is read from its ``price_collection`` activity records (W6-D, D222) of the last 24 hours.

These are cached for 60 seconds behind a fingerprint of the records they read, like HQ's money facts
(``hq_money``), so a poll is cheap and a new fill, ledger event, day-start value, stop, owner policy, receipt or rate
shows on the next poll; the collector records and prices refresh within the minute. Reading HQ starts nothing:
plain reads inside the request's snapshot, no broker, scheduler, provider or engine is called, and nothing is
written (tested). A failure is never invented away: the part says it can't be worked out, never zero.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from time import monotonic
from typing import Any

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import (
    COLLECTOR_LINES_MAX,
    SCORECARD_FACTS_MAX,
    SCORECARD_LINES_MAX,
    SCORECARD_PROVISIONAL_MAX,
    iso_ms,
)
from trade_graph.application.ledger import Ledger
from trade_graph.application.money_limits import MoneyLimitGuard
from trade_graph.application.scorecard import GATES_STATEMENT, ScorecardUnavailable, compute_scorecard
from trade_graph.contracts.models import OwnerPolicy
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.money_limits import trading_day

MAX_AGE_SECONDS = 60
_monotonic = monotonic  # injectable timer (tests move it forward)
FAILURES = (sqlite3.DatabaseError, ValueError, TypeError, ArithmeticError, LookupError, TradeGraphError)
COLLECTOR_WINDOW = timedelta(hours=24)
COLLECTOR_ROWS_MAX = 2000  # collector records read per refresh; more in 24 hours reads "at least"
ACTIVITY_SCAN_MAX = 200_000  # activity rows looked at, newest first, to find them
SCAN_FRESH = timedelta(hours=2)  # the coin rule's bound for a scan (W2-B, D183)
HUNDRED = Decimal("100")
CENT = Decimal("0.01")
HALF_CENT = Decimal("0.005")
TENTH = Decimal("0.1")
_EXACT = Context(prec=120)
T = c.TRADING
L, S, K = T["loss"], T["scorecard"], T["collector"]


# --- The cache ----------------------------------------------------------------------------------------------------


def facts(runtime, now: datetime) -> dict:
    """The cached raw facts for ``runtime`` (at most ``MAX_AGE_SECONDS`` old, refreshed early on a new record)."""
    lock = getattr(runtime, "_hq_trading_lock", None)
    if lock is None:
        lock = runtime._hq_trading_lock = threading.Lock()
    with lock:
        try:
            current = fingerprint(runtime)
        except FAILURES:
            current = None
        cached = getattr(runtime, "_hq_trading", None)
        if cached and current is not None and _monotonic() - cached[0] < MAX_AGE_SECONDS and cached[1] == current:
            return cached[2]
        result = compute(runtime, now)
        runtime._hq_trading = (_monotonic(), current, result)
        return result


def fingerprint(runtime) -> tuple:
    """Changes when a fill, ledger event, day-start value, stop, owner policy, AI receipt or reference rate lands."""
    database, pid = runtime.database, runtime.portfolio_id
    tables = _tables(database)

    def newest(table: str, where: str = "", params: tuple = ()) -> Any:
        if table not in tables:
            return None
        return database.execute(f"SELECT MAX(rowid) FROM {table} {where}", params).fetchone()[0]

    return (
        newest("fills", "WHERE portfolio_id = ?", (pid,)), newest("ledger_events"), newest("trading_day_values"),
        newest("daily_loss_stops"), newest("owner_policy_revisions"), newest("usage_receipts"), newest("fx_rates"),
        tuple(database.execute("SELECT MAX(rowid), MAX(updated_at) FROM budget_reservations").fetchone())
        if "budget_reservations" in tables else None,
    )


def compute(runtime, now: datetime) -> dict:
    """The raw facts: plain values only, so the cache never holds a live object."""
    paper = _paper(runtime)
    return {"as_of": now, "paper": paper,
            "loss": _loss(runtime, now) if paper else None,
            "scorecard": _scorecard(runtime, now) if paper else None,
            "collector": _collector(runtime, now)}


def _tables(database) -> set[str]:
    return {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _paper(runtime) -> bool:
    row = runtime.database.execute("SELECT mode FROM portfolios WHERE portfolio_id = ?",
                                   (runtime.portfolio_id,)).fetchone()
    return row is not None and row["mode"] == "paper"


def _policy(database) -> OwnerPolicy | None:
    """The active owner policy, as ``AuthorityRecord.active_policy`` reads it (the newest revision)."""
    row = database.execute(
        "SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    return None if row is None else OwnerPolicy.model_validate_json(row["document_json"])


def _loss(runtime, now: datetime) -> dict:
    empty = {"recorded": False, "limits": None, "document": None, "error": None}
    try:
        policy = _policy(runtime.database)
    except FAILURES as exc:
        return {**empty, "error": type(exc).__name__}
    limits = None if policy is None else policy.money_limits
    if limits is None:
        return empty
    frame = {"fraction": canonical_decimal(limits.daily_loss_limit_fraction), "currency": limits.account_currency,
             "day_start": limits.trading_day_start_utc}
    ledger = getattr(runtime, "ledger", None) or Ledger(runtime.database, runtime.clock)
    try:
        status = MoneyLimitGuard(runtime.database, ledger, runtime.clock).status(runtime.portfolio_id, policy)
    except FAILURES as exc:
        return {"recorded": True, "limits": frame, "document": None, "error": type(exc).__name__}
    return {"recorded": True, "limits": frame, "document": None if status is None else status.document(),
            "error": None}


def _scorecard(runtime, now: datetime) -> dict:
    try:
        card = compute_scorecard(runtime.database, portfolio_id=runtime.portfolio_id, end=now)
    except ScorecardUnavailable as exc:
        return {"document": None, "unavailable": str(exc)}
    except FAILURES:
        return {"document": None, "unavailable": None}
    return {"document": card.document(), "unavailable": None}


def _collector(runtime, now: datetime) -> dict:
    """The ``price_collection`` records of the last 24 hours, newest first, read without parsing anything else."""
    config = getattr(runtime, "config", None)
    collector = getattr(config, "price_collector", None)
    enabled = getattr(collector, "enabled", None)
    enabled = enabled if isinstance(enabled, bool) else None
    cutoff = utc_iso(now - COLLECTOR_WINDOW)
    events, truncated = [], False
    try:
        rows = runtime.database.execute(
            """SELECT created_at, CASE WHEN kind = 'price_collection' AND (portfolio_id = ? OR portfolio_id IS NULL)
               THEN payload_json END AS payload FROM activity_events ORDER BY rowid DESC LIMIT ?""",
            (runtime.portfolio_id, ACTIVITY_SCAN_MAX))
        for row in rows:
            if isinstance(row["created_at"], str) and row["created_at"] < cutoff:
                break
            if row["payload"] is None:
                continue
            if len(events) >= COLLECTOR_ROWS_MAX:
                truncated = True
                break
            payload = json.loads(row["payload"])
            if isinstance(payload, dict) and isinstance(row["created_at"], str):
                events.append({"created_at": row["created_at"], "payload": payload})
    except (sqlite3.DatabaseError, ValueError, TypeError):
        events, truncated = [], False
    return {"enabled": enabled, "events": events, "truncated": truncated}


# --- Small helpers ------------------------------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _percent_text(fraction: Decimal) -> str:
    """A fraction as a percent for words: "4", "2.1", "0.05" (exact up to two places, no trailing zeros)."""
    value = (fraction * HUNDRED).quantize(CENT, rounding=ROUND_HALF_EVEN)
    if abs(value) >= 1:
        value = value.quantize(TENTH, rounding=ROUND_HALF_EVEN)
    return canonical_decimal(value) + "%"


def _usd_words(amount: Decimal, currency: str) -> dict:
    """An amount in words; a cost under half a cent reads "under $0.01", so it never reads as $0.00."""
    if Decimal("0") < amount < HALF_CENT and currency == "USD":
        return c.text(S["under_a_cent"])
    return c.money_segment(amount, currency)


def _forms(forms: tuple[str, str], count: int, **values: Any) -> str:
    return (forms[0] if count == 1 else forms[1]).format(n=count, **values)


def _first_upper(value: str) -> str:
    value = value.strip()
    return value[:1].upper() + value[1:] if value else value


def _sentence(value: str) -> str:
    value = _first_upper(" ".join(value.split()))
    return value if value.endswith((".", "!", "?")) else value + "."


# --- Today's loss -------------------------------------------------------------------------------------------------


def _loss_frame(limits: dict, now: datetime) -> dict:
    day = trading_day(now, limits["day_start"])
    return {"currency": limits["currency"], "limit_percent": canonical_decimal(Decimal(limits["fraction"]) * HUNDRED),
            "day_start": iso_ms(day.start), "resumes_at": iso_ms(day.end)}


def _empty_loss(state: str, headline: str, detail: str) -> dict:
    return {"state": state, "currency": None, "limit_percent": None, "day_start": None, "resumes_at": None,
            "day_start_value": None, "day_start_basis": None, "value": None, "loss": None, "limit_amount": None,
            "used_percent": None, "headline": c.says(headline), "detail": c.says(detail)}


def _unknown_words(reason: str | None, error: str | None) -> str:
    words = L["unknown_reason"]
    if error is not None:
        return words["error"]
    text = reason or ""
    if text.startswith("the day-start value"):
        return words["start"]
    if "has no" in text and "mark" in text:
        return words["missing"]
    if "valuation is stale" in text:
        return words["stale"]
    if "too old" in text:
        return words["old"]
    return words["other"]


def loss_view(loss: dict | None, now: datetime, *, paper: bool) -> dict:
    """The ``LossLimit`` view of the guard's reading (``loss`` as ``compute`` returns it)."""
    if not paper:
        return _empty_loss("not_applied", *L["not_applied"])
    if loss is None or not loss["recorded"]:
        return _empty_loss("not_recorded", *L["not_recorded"])
    document = loss.get("document")
    frame = _loss_frame(loss["limits"], now)
    currency = frame["currency"]
    if document is not None:
        frame.update(currency=document["currency"], day_start=iso_ms(document["day_start"]),
                     resumes_at=iso_ms(document["resumes_at"]),
                     limit_percent=canonical_decimal(Decimal(document["limit_fraction"]) * HUNDRED))
        currency = document["currency"]
    start = _decimal(document.get("day_start_value")) if document else None
    value = _decimal(document.get("value")) if document else None
    limit = _decimal(document.get("limit_amount")) if document else None
    # The loss is worked out here, exactly, from the two values the guard read (the contract checks it).
    amount = None if start is None or value is None else _EXACT.subtract(start, value)
    used = None
    if amount is not None and limit is not None and limit > 0:
        used = float(min(HUNDRED, max(Decimal("0"), amount / limit * HUNDRED)))
    basis = document.get("day_start_basis") if document and start is not None else None
    stopped = bool(document and document.get("stopped"))
    reached = bool(document and document.get("reached")) and not stopped
    if stopped:
        state = "stopped"
    elif reached:
        state = "reached"
    elif amount is None or limit is None:
        state = "unknown"
    else:
        state = "within"
    view = {**frame, "state": state, "day_start_value": c.money(start, currency), "day_start_basis": basis,
            "value": c.money(value, currency), "loss": c.money(amount, currency),
            "limit_amount": c.money(limit, currency), "used_percent": used}
    resumes = frame["resumes_at"]
    percent = frame["limit_percent"] + "%"
    if state == "stopped":
        first, second, third, fourth = L["stopped"]
        headline = c.says(first, c.term("loss_limit", second), third, c.time(resumes, "day_clock"), fourth)
        if amount is not None and limit is not None and amount > 0:
            before, middle, after = L["stopped_figures"]
            headline = c.says(*headline, before, c.money_segment(amount, currency), middle,
                              c.money_segment(limit, currency), after)
        detail = c.says(L["stopped_detail"])
    elif state == "reached":
        first, second, third, fourth, fifth = L["reached"]
        headline = c.says(first, c.money_segment(amount, currency), second, c.money_segment(limit, currency), third,
                          percent, fourth, c.time(resumes, "day_clock"), fifth)
        detail = c.says(L["reached_detail"])
    elif state == "unknown":
        headline = c.says(L["unknown"])
        detail = c.says(_unknown_words(document.get("unknown") if document else None, loss.get("error")),
                        L["unknown_tail"])
    else:
        headline = _within_headline(amount, limit, start, currency, percent)
        before, after = L["within"]
        detail = c.says(before, c.time(resumes, "day_clock"), after)
    if basis == "first_valuation_after_start" and document and document.get("day_start_valued_at"):
        before, after = L["first_valuation"]
        detail = c.says(*detail, before, c.time(document["day_start_valued_at"], "clock"), after)
    view.update(headline=headline, detail=detail)
    return view


def _within_headline(amount: Decimal, limit: Decimal, start: Decimal, currency: str, percent: str) -> list[dict]:
    if amount > 0:
        first, second, third, fourth, fifth, sixth = L["loss"]
        return c.says(first, c.term("loss_limit", "loss"), second, c.money_segment(amount, currency), third,
                      c.money_segment(limit, currency), fourth, percent, fifth, c.money_segment(start, currency),
                      sixth)
    if amount < 0:
        first, second, third, fourth, fifth, sixth, seventh = L["gain"]
        return c.says(first, c.money_segment(-amount, currency), second, c.term("loss_limit", third), fourth,
                      c.money_segment(limit, currency), fifth, percent, sixth, c.money_segment(start, currency),
                      seventh)
    first, second, third, fourth, fifth, sixth = L["even"]
    return c.says(first, c.term("loss_limit", second), third, c.money_segment(limit, currency), fourth, percent,
                  fifth, c.money_segment(start, currency), sixth)


# --- The scorecard ------------------------------------------------------------------------------------------------


def _unavailable_card(title: str, reason: list[dict]) -> dict:
    return {"available": False, "unavailable_reason": reason, "status": None, "period_start": None,
            "period_end": None, "currency": None, "result": None, "versus_btc": None, "beats_cash": None,
            "beats_btc": None, "headline": c.says(title), "facts": [], "lines": [], "provisional": [],
            "more_provisional": 0, "note": c.says(GATES_STATEMENT)}


def scorecard_view(scorecard: dict | None, *, paper: bool) -> dict:
    """The ``Scorecard`` view of ``Scorecard.document()`` (``scorecard`` as ``compute`` returns it)."""
    if not paper:
        return _unavailable_card(S["live_title"], c.says(S["live"]))
    document = (scorecard or {}).get("document")
    if document is None:
        reason = (scorecard or {}).get("unavailable")
        return _unavailable_card(S["unavailable_title"], c.says(_sentence(reason) if reason else S["error"]))
    currency = document["currency"]
    metrics = document["metrics"]
    result = Decimal(metrics["result"])
    btc = document["benchmarks"]["hold_btc"]
    versus = _decimal(btc.get("versus")) if btc.get("available") else None
    reasons = [str(item) for item in document.get("provisional_reasons") or []]
    return {
        "available": True, "unavailable_reason": None, "status": document["status"],
        "period_start": iso_ms(document["period"]["start"]), "period_end": iso_ms(document["period"]["end"]),
        "currency": currency, "result": c.money(result, currency),
        "versus_btc": c.money(versus, currency), "beats_cash": result > 0,
        "beats_btc": None if versus is None else versus > 0,
        "headline": _card_headline(document, result),
        "facts": _card_facts(metrics, currency)[:SCORECARD_FACTS_MAX],
        "lines": _card_lines(document, result, versus)[:SCORECARD_LINES_MAX],
        "provisional": [c.says(_sentence(reason)) for reason in reasons[:SCORECARD_PROVISIONAL_MAX]],
        "more_provisional": max(0, len(reasons) - SCORECARD_PROVISIONAL_MAX),
        "note": c.says(GATES_STATEMENT),
    }


def _card_headline(document: dict, result: Decimal) -> list[dict]:
    currency = document["currency"]
    before, after = S["since"]
    head = [before, c.time(document["period"]["start"], "date"), after]
    fraction = _decimal(document["metrics"].get("result_fraction"))
    percent = _percent_text(abs(fraction)) if fraction is not None else None
    if result == 0:
        return c.says(*head, S["even"])
    first, middle, last = S["up" if result > 0 else "down"]
    if percent is None:
        return c.says(*head, first, c.money_segment(abs(result), currency), S["after_fees"])
    return c.says(*head, first, c.money_segment(abs(result), currency), middle, percent, last)


def _fact(key: str, *value: str | dict) -> dict:
    return {"label": S["labels"][key], "value": c.says(*value)}


def _card_facts(metrics: dict, currency: str) -> list[dict]:
    trades, wins, losses = int(metrics["trades"]), int(metrics["wins"]), int(metrics["losses"])
    even = int(metrics.get("flat_trades") or 0)
    facts = []
    if trades:
        forms = S["trades"][1 if even else 0]
        facts.append(_fact("trades", forms.format(n=trades, won=wins, lost=losses, even=even)))
        rate = _decimal(metrics.get("win_rate"))
        if rate is not None:
            facts.append(_fact("won", _percent_text(rate)))
    else:
        facts.append(_fact("trades", S["no_trades"]))
    for key, field in (("average_win", "average_win"), ("average_loss", "average_loss")):
        amount = _decimal(metrics.get(field))
        if amount is not None:
            facts.append(_fact(key, c.money_segment(amount, currency)))
    facts.append(_fact("fees", c.money_segment(Decimal(metrics["fees_paid"]), currency)))
    drawdown = metrics.get("worst_drawdown") or {}
    fall, share = _decimal(drawdown.get("amount")), _decimal(drawdown.get("fraction"))
    if fall is not None and share is not None and fall > 0:
        facts.append(_fact("fall", _percent_text(share), " (", c.money_segment(fall, currency), ")"))
    else:
        facts.append(_fact("fall", S["none"]))
    streak = int(metrics.get("worst_losing_streak") or 0)
    facts.append(_fact("streak", _forms(S["run"], streak) if streak else S["none"]))
    held = len(metrics.get("open_positions") or [])
    if held:
        facts.append(_fact("held", _forms(S["held"], held)))
    return facts


def _card_lines(document: dict, result: Decimal, versus: Decimal | None) -> list[list[dict]]:
    currency = document["currency"]
    lines = []
    if result > 0:
        before, after = S["cash_ahead"]
        lines.append(c.says(before, c.money_segment(result, currency), after))
    elif result < 0:
        before, after = S["cash_behind"]
        lines.append(c.says(before, c.money_segment(-result, currency), after))
    else:
        lines.append(c.says(S["cash_even"]))
    btc = document["benchmarks"]["hold_btc"]
    hold = (document["metrics"].get("benchmarks") or {}).get("hold_btc") or {}
    if versus is not None:
        first, second, third = S["btc_ahead" if versus >= 0 else "btc_behind"]
        made = _decimal(hold.get("result"))
        line = [first, c.money_segment(abs(versus), currency)]
        if made is not None:
            line += [second, c.money_segment(made, currency)]
        lines.append(c.says(*line, third))
    else:
        lines.append(c.says(S["btc_missing"].format(reason=_plain_reason(btc.get("reason")))))
    lines.append(_eur_line(document.get("eur_reporting") or {}))
    lines.append(_ai_line(document.get("ai_spending") or {}))
    lines.append(_rule_line(document.get("owner_test_reading") or {}))
    return lines


def _plain_reason(reason: Any) -> str:
    text = " ".join(str(reason or "").split()).rstrip(".")
    return text or "the records don't say why"


def _eur_line(eur: dict) -> list[dict]:
    if not eur.get("available"):
        return c.says(S["eur_missing"].format(reason=_plain_reason(eur.get("reason"))))
    currency = eur.get("currency") or "EUR"
    result, effect, versus = (_decimal(eur.get(name)) for name in ("result", "currency_effect", "versus_cash"))
    if result is None or effect is None or versus is None:
        return c.says(S["eur_missing"].format(reason="a figure is missing"))
    first, second, third, fourth = S["eur"]
    line = c.says(first, c.money_segment(result, currency), second, c.money_segment(effect, currency), third,
                  c.money_segment(versus, currency), fourth)
    return c.says(*line, S["eur_stale"]) if eur.get("stale") else line


def _ai_line(spending: dict) -> list[dict]:
    total = spending.get("total") or {}
    calls = int(total.get("calls") or 0)
    if not calls:
        return c.says(S["ai_none"])
    actual = _decimal(total.get("actual_usd")) or Decimal("0")
    estimated = _decimal(total.get("estimated_usd")) or Decimal("0")
    open_calls = int(total.get("unresolved_calls") or 0)
    held = _decimal(total.get("unresolved_usd"))
    first, known, plus, unknown, last = S["ai"]
    line = [first, _usd_words(actual + estimated, "USD"), known]
    if open_calls and held is not None and held > 0:
        line += [plus, _usd_words(held, "USD"), unknown]
    elif open_calls:
        line.append(_forms(S["ai_unvalued"], open_calls))
    return c.says(*line, last)


def _days_words(value: Any) -> str:
    days = _decimal(value)
    if days is None:
        return S["days_unknown"]
    shown = canonical_decimal(days.quantize(TENTH, rounding=ROUND_HALF_EVEN))
    return S["days"][0] if shown == "1" else S["days"][1].format(n=shown)


def _rule_line(reading: dict) -> list[dict]:
    trades = int(reading.get("trades") or 0)
    words = S["rule"].format(days=reading.get("minimum_days", 28), trades=reading.get("minimum_trades", 30),
                             so_far_days=_days_words(reading.get("days")),
                             so_far_trades=_forms(S["completed"], trades))
    if not reading.get("long_enough"):
        words += S["rule_early"]
    elif reading.get("keep_going") is True:
        words += S["rule_keep"]
    elif reading.get("keep_going") is False:
        words += S["rule_stop"]

    def answer(value: Any) -> str:
        return S["yes"] if value is True else S["no"] if value is False else S["cant_tell"]

    words += S["rule_beats"].format(cash=answer(reading.get("beats_cash")), btc=answer(reading.get("beats_btc")))
    return c.says(words)


# --- The price collector ------------------------------------------------------------------------------------------


def _problem(payload: dict) -> bool:
    return (payload.get("status") in ("failed", "partial") or bool(payload.get("gaps"))
            or payload.get("stop_reason") in ("rate_limited", "throttled") or bool(payload.get("ticker_error")))


def collector_view(collector: dict | None, now: datetime) -> dict:
    collector = collector or {"enabled": None, "events": [], "truncated": False}
    events = [item for item in collector.get("events") or [] if isinstance(item.get("payload"), dict)]
    if collector.get("enabled") is False:
        last = events[0]["created_at"] if events else None
        scan = next((item["created_at"] for item in events if item["payload"].get("job") == "scan"), None)
        return {"state": "off", "headline": c.says(K["off"]), "last_at": _ms(last), "last_scan_at": _ms(scan),
                "problems_24h": sum(_problem(item["payload"]) for item in events), "lines": []}
    if not events:
        return {"state": "no_records", "headline": c.says(K["no_records"]), "last_at": None, "last_scan_at": None,
                "problems_24h": 0, "lines": []}
    newest = {}
    for item in events:  # newest first
        newest.setdefault(item["payload"].get("job"), item)
    scan, candles, catalog = newest.get("scan"), newest.get("candles"), newest.get("catalog")
    problems = sum(_problem(item["payload"]) for item in events)
    issues, lines = [], []
    words = K["problem_words"]
    scan_at = parse_utc(scan["created_at"]) if scan else None
    if scan is not None:
        status = scan["payload"].get("status")
        key = "scan_ok" if status == "ok" else "scan_empty" if status == "no_candidates" else "scan_failed"
        before, after = K[key]
        lines.append(c.says(before, c.time(scan["created_at"], "clock"), after))
        if key == "scan_failed":
            issues.append(words["scan"])
    if scan_at is None or now - scan_at > SCAN_FRESH:
        lines.append(c.says(K["scan_old"]))
        issues.append(words["scan_old"])
    if candles is not None:
        payload = candles["payload"]
        if payload.get("error") == "clock_regression":
            key, issue = "candles_clock", words["clock"]
        elif payload.get("status") == "failed":
            key, issue = "candles_failed", words["candles_failed"]
        elif payload.get("status") == "partial":
            key, issue = "candles_partial", words["candles_partial"]
        else:
            key, issue = "candles_ok", None
        before, after = K[key]
        lines.append(c.says(before, c.time(candles["created_at"], "clock"), after))
        if issue:
            issues.append(issue)
    if catalog is not None:
        failed = catalog["payload"].get("status") == "failed"
        before, after = K["catalog_failed" if failed else "catalog_ok"]
        lines.append(c.says(before, c.time(catalog["created_at"], "day_clock"), after))
        if failed:
            issues.append(words["catalog"])
    limited = sum(item["payload"].get("stop_reason") in ("rate_limited", "throttled") for item in events)
    if limited:
        lines.append(c.says(_forms(K["rate_limited"], limited)))
    gaps = sum(int(item["payload"].get("gaps") or 0) for item in events
               if isinstance(item["payload"].get("gaps"), int) and not isinstance(item["payload"].get("gaps"), bool))
    if gaps:
        lines.append(c.says(_forms(K["gaps"], gaps)))
    if collector.get("truncated"):
        lines.append(c.says(K["problems_many"].format(n=problems)))
    else:
        lines.append(c.says(_forms(K["problems"], problems) if problems else K["no_problems"]))
    if issues:
        headline = c.says(K["problem"].format(problem=issues[0]))
    elif scan is not None:
        before, after = K["working_scan"]
        headline = c.says(before, c.time(scan["created_at"], "relative"), after)
    else:
        before, after = K["working"]
        headline = c.says(before, c.time(events[0]["created_at"], "relative"), after)
    return {"state": "attention" if issues else "ok", "headline": headline, "last_at": _ms(events[0]["created_at"]),
            "last_scan_at": _ms(scan["created_at"] if scan else None), "problems_24h": problems,
            "lines": lines[:COLLECTOR_LINES_MAX]}


def _ms(value: str | None) -> str | None:
    return None if value is None else iso_ms(value)


# --- The block and the notice -------------------------------------------------------------------------------------


def block(facts: dict) -> dict:
    now, paper = facts["as_of"], facts["paper"]
    return {"as_of": iso_ms(now), "loss": loss_view(facts.get("loss"), now, paper=paper),
            "scorecard": scorecard_view(facts.get("scorecard"), paper=paper),
            "collector": collector_view(facts.get("collector"), now)}


def notices(facts: dict) -> list[dict]:
    """A notice on the HQ tab while today's loss limit stops new buying (nothing for the owner to do)."""
    if not facts.get("paper"):
        return []
    view = loss_view(facts.get("loss"), facts["as_of"], paper=True)
    if view["state"] not in ("reached", "stopped"):
        return []
    before, after = L["notice_text"]
    return [{"code": "loss_limit_reached", "tone": "attention", "title": c.says(L["notice_title"]),
             "text": c.says(before, c.time(view["resumes_at"], "day_clock"), after), "link": "/trading"}]


__all__ = ["MAX_AGE_SECONDS", "block", "collector_view", "compute", "facts", "fingerprint", "loss_view", "notices",
           "scorecard_view"]
