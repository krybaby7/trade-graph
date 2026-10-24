"""Explicitly requested public hourly history collection, independent of trading."""

from __future__ import annotations

from datetime import UTC, timedelta

from trade_graph.adapters.market.kraken_history import (
    SUPPORTED_SYMBOLS,
    KrakenHourlyHistory,
    validate_history_hours,
)
from trade_graph.adapters.market.public import TextTransport
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.price_history import HistoryStore
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure


def _coverage(candles: list[HourlyCandle], clock: Clock, hours: int) -> dict:
    received_at = (candles[0].available_at_utc if candles else clock.now()).astimezone(UTC)
    end = received_at.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    known = {candle.open_time_utc for candle in candles}
    gaps = []
    for index in range(hours):
        opened_at = start + timedelta(hours=index)
        if opened_at not in known:
            gaps.append({
                "open_time_utc": utc_iso(opened_at), "close_time_utc": utc_iso(opened_at + timedelta(hours=1)),
            })
    return {
        "fetched": len(candles),
        "requested_hours": hours,
        "window_start_utc": utc_iso(start),
        "expected_latest_close_utc": utc_iso(end),
        "available_at_utc": utc_iso(received_at),
        "earliest_open_utc": utc_iso(candles[0].open_time_utc) if candles else None,
        "latest_closed_utc": utc_iso(candles[-1].close_time_utc) if candles else None,
        "missing_hours": len(gaps),
        "gaps": gaps,
        "insufficient_history": bool(gaps),
    }


def collect_public_hourly_history(
    database: Database,
    clock: Clock,
    transport: TextTransport,
    *,
    hours: int = 168,
    symbols: tuple[str, ...] = SUPPORTED_SYMBOLS,
) -> dict:
    """Collect at most two responses and commit each valid symbol atomically.

    Successful observations remain retained if the other request fails. Reports
    use fixed failure categories; response bodies and transport exception details
    are never exposed. Sparse responses are useful observations, explicitly
    reported as insufficient history instead of invented or filled candles.
    """
    validate_history_hours(hours)
    if (not isinstance(symbols, tuple) or not 1 <= len(symbols) <= len(SUPPORTED_SYMBOLS)
            or any(not isinstance(symbol, str) or symbol not in SUPPORTED_SYMBOLS for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValidationFailure("hourly history requires a unique bounded BTC/USD and ETH/USD scope")
    history = KrakenHourlyHistory(transport, clock)
    store = HistoryStore(database)
    report = {
        "status": "ok",
        "source": "kraken_public_ohlc",
        "interval_minutes": 60,
        "request_count": 0,
        "paid_calls_enabled": False,
        "live_trading_enabled": False,
        "symbols": {},
    }
    failures = 0
    for symbol in symbols:
        report["request_count"] += 1
        try:
            candles = history.fetch(symbol, hours=hours)
        except ValidationFailure:
            failures += 1
            report["symbols"][symbol] = {"status": "failed", "error": "public_history_invalid"}
            continue
        except Exception:
            failures += 1
            report["symbols"][symbol] = {"status": "failed", "error": "public_history_request_failed"}
            continue
        try:
            saved = store.save(candles)
        except Exception:
            failures += 1
            report["symbols"][symbol] = {"status": "failed", "error": "history_persistence_failed"}
            continue
        report["symbols"][symbol] = {"status": "ok", **_coverage(candles, clock, hours), **saved}
    if failures:
        report["status"] = "failed" if failures == len(symbols) else "partial"
    return report
