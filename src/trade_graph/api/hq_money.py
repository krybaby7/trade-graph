"""Fingerprinted 60-second cache of HQ's expensive facts (spec §B.3).

Holds raw values only (no copy, no viewer): the economics from one
``financial.overview``, whether positions are held, activity counts, the active
mandate, the allowance stop line, the attempt limit and the hourly-history
averages. The cache is recomputed after 60 seconds or as soon as a bill, fill,
ledger event, order or decision lands. Open orders are not cached: the projection
reads them on every poll, so "flat" is always fresh. A failure leaves the money
values unknown (``None``), never zero.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from decimal import Decimal
from time import monotonic

from trade_graph.api import controls, live
from trade_graph.api.hq_chart import candle_store_for
from trade_graph.application.price_history import CandleStoreHistory, HistoryStore
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError

MAX_AGE_SECONDS = 60
_monotonic = monotonic  # injectable timer (tests move it forward)
MARKET_SYMBOLS = ("BTC/USD", "ETH/USD")
FAILURES = (ValueError, TypeError, ArithmeticError, LookupError, TradeGraphError)


def money(runtime, now: str) -> dict:
    """The cached money facts for ``runtime``; ``now`` is the request's ISO instant."""
    lock = getattr(runtime, "_hq_money_lock", None)
    if lock is None:
        lock = runtime._hq_money_lock = threading.Lock()
    with lock:
        current = fingerprint(runtime)
        cached = getattr(runtime, "_hq_money", None)
        if cached and _monotonic() - cached[0] < MAX_AGE_SECONDS and cached[1] == current:
            return cached[2]
        result = compute(runtime, now)
        runtime._hq_money = (_monotonic(), current, result)
        return result


def fingerprint(runtime) -> tuple:
    database, pid = runtime.database, runtime.portfolio_id
    return (
        database.execute("SELECT MAX(sequence) FROM owner_expense_evidence").fetchone()[0],
        database.execute("SELECT MAX(rowid) FROM fills WHERE portfolio_id=?", (pid,)).fetchone()[0],
        database.execute("SELECT MAX(rowid) FROM ledger_events").fetchone()[0],
        database.execute("SELECT MAX(rowid) FROM order_intents WHERE portfolio_id=?", (pid,)).fetchone()[0],
        database.execute("SELECT MAX(rowid) FROM decisions WHERE portfolio_id=?", (pid,)).fetchone()[0],
    )


def compute(runtime, now: str) -> dict:
    reader = live.projection_runtime(runtime)
    result = {"as_of": now, **_economics(runtime)}
    result["nonflat_positions"] = _nonflat_positions(reader)
    result["counts"] = _counts(runtime, now)
    result["mandate"] = _mandate(runtime)
    result.update(_threshold(runtime))
    result["max_attempts"] = _max_attempts(runtime)
    result["market"] = _market(runtime)
    return result


def _economics(runtime) -> dict:
    try:
        economic = live.economics(runtime)
    except FAILURES as exc:
        return {"available": False, "error": type(exc).__name__}
    data = economic["financial"]
    valuation = data.get("valuation") or {}
    reporting = data["reporting_currency"]
    capital = data.get("allocated_capital") or {}
    current = data.get("current_value") or {}
    benchmark = data.get("benchmark") or {}
    result = economic["economic_result"]
    return {
        "available": True, "error": None,
        "computed_at": economic["now"],
        "reporting_currency": reporting,
        "equity": data.get("equity"),
        "valuation_provisional": bool(valuation.get("provisional")),
        "fx": [_fx(row) for row in valuation.get("fx", []) if row.get("quote") == reporting],
        "marks": [{"asset": row["asset"], "stale": bool(row["stale"]), "age_seconds": row.get("age_seconds"),
                   "observed_at": row.get("observed_at")} for row in valuation.get("marks", [])],
        "opening_native": list(capital.get("native") or []),
        "opening_reporting": capital.get("reporting_amount"),
        "opening_at": capital.get("at"),
        "trading_pnl": (data.get("performance") or {}).get("trading_pnl"),
        "fx_pnl": benchmark.get("fx_pnl"),
        "strategy_alpha": benchmark.get("strategy_alpha"),
        "inventory": current.get("inventory"),
        "cash": current.get("cash"),
        "economic_status": result["status"],
        "net": result["net_result"],
        "codes": list(economic["unknown_reason_codes"]),
        "recovery_status": (economic["recovery_history"] or {}).get("status"),
    }


def _fx(row: dict) -> dict:
    maximum = row.get("maximum_age_seconds")
    age = row.get("freshness_age_seconds")
    return {"base": row.get("base"), "valid_as_of": row.get("valid_as_of"),
            "recorded_stale": bool(row.get("recorded_stale")),
            "expired": maximum is not None and age is not None and age > maximum}


def _nonflat_positions(reader) -> bool | None:
    """Whether anything is held (lots or non-cash balances); open orders are read fresh by the projection."""
    try:
        return bool(controls._nonflat(reader))
    except (*FAILURES, AttributeError):
        return None


def _counts(runtime, now: str) -> dict:
    database, pid = runtime.database, runtime.portfolio_id
    actions = {row["action"]: row["n"] for row in database.execute(
        "SELECT action, COUNT(*) AS n FROM decisions WHERE portfolio_id=? GROUP BY action", (pid,)).fetchall()}
    findings = database.execute(
        """SELECT COUNT(*) AS total, COALESCE(SUM(available_at <= ? AND expires_at > ?
               AND COALESCE(json_extract(document_json, '$.relevance'), '') != 'untrusted-page'), 0) AS in_use
        FROM findings WHERE portfolio_id=? OR portfolio_id IS NULL""", (now, now, pid)).fetchone()
    waits = actions.get("no_action", 0) + actions.get("hold", 0)
    buys, sells = actions.get("enter", 0), actions.get("exit", 0)
    total = sum(actions.values())
    return {
        "decisions": total, "waits": waits, "buys": buys, "sells": sells,
        "other_decisions": total - waits - buys - sells,
        "orders": database.execute("SELECT COUNT(*) FROM order_intents WHERE portfolio_id=?", (pid,)).fetchone()[0],
        "completed_trades": database.execute("SELECT COUNT(*) FROM fills WHERE portfolio_id=?", (pid,)).fetchone()[0],
        "research_notes": findings["total"], "notes_in_use": findings["in_use"],
        "lessons": database.execute("SELECT COUNT(DISTINCT lesson_id) FROM lessons WHERE portfolio_id=?",
                                    (pid,)).fetchone()[0],
    }


def _mandate(runtime) -> dict | None:
    row = runtime.database.execute(
        """SELECT document_json, expires_at FROM mandates WHERE portfolio_id=? AND active=1
        ORDER BY revision DESC LIMIT 1""", (runtime.portfolio_id,)).fetchone()
    if row is None:
        return None
    try:
        document = json.loads(row["document_json"])
    except (TypeError, ValueError):
        document = {}
    document = document if isinstance(document, dict) else {}
    symbols = document.get("symbols")
    return {"symbols": [item for item in symbols if isinstance(item, str)] if isinstance(symbols, list) else [],
            "max_gross_exposure_fraction": document.get("max_gross_exposure_fraction"),
            "expires_at": row["expires_at"]}


def _threshold(runtime) -> dict:
    """The allowance stop line: static policy configuration only, never cached admission or readiness."""
    status = getattr(runtime, "subscription_readonly_status", None)
    if not callable(status):
        return {"threshold": None, "threshold_enabled": None}
    try:
        policy = status()["subscription"]["quota_reserve_policy"]
        threshold, enabled = policy["admission_threshold_remaining_percent"], policy["enabled"]
    except (*FAILURES, AttributeError):
        return {"threshold": None, "threshold_enabled": None}
    if type(threshold) not in (int, float) or not 0 <= threshold <= 100 or type(enabled) is not bool:
        return {"threshold": None, "threshold_enabled": None}
    return {"threshold": threshold if enabled else None, "threshold_enabled": enabled}


def _max_attempts(runtime) -> int | None:
    row = runtime.database.execute(
        "SELECT document_json FROM owner_policy_revisions ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["document_json"]).get("ordinary_max_paid_attempts")
    except (TypeError, ValueError, AttributeError):
        return None
    return value if type(value) is int and value >= 0 else None


def _market(runtime) -> dict:
    # D220: the trend label reads the candle file when HQ has one (the collector keeps it current), else hourly_candles.
    candles, status = candle_store_for(runtime)
    history = CandleStoreHistory(candles) if candles is not None and status == "connected" else HistoryStore(
        runtime.database)
    at: datetime = runtime.clock.now()
    result = {}
    for symbol in MARKET_SYMBOLS:
        try:
            snapshot = history.snapshot(symbol, at, ["sma_20", "sma_50"])
        except FAILURES:
            result[symbol] = {"sma_20": None, "sma_50": None}
            continue
        values = snapshot.get("values") or {}
        result[symbol] = {name: _decimal_text(values.get(name)) for name in ("sma_20", "sma_50")}
    result["observed_at"] = utc_iso(at)
    return result


def _decimal_text(value) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return str(value) if number.is_finite() else None
