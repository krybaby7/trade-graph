"""Shared bounded point-in-time market inputs for Research and Trader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from trade_graph.adapters.market.replay import quote_features
from trade_graph.application.candle_readiness import expected_candle_status
from trade_graph.application.price_history import FEATURE_VERSION, HistoryStore, HourlyHistory
from trade_graph.domain.clock import utc_iso


def active_templates(templates: dict, guard: dict) -> dict:
    """Only the current mandate's template requirements select historical inputs."""
    return {strategy_id: templates[strategy_id] for strategy_id in guard["mandate"]["strategy_ids"]
            if strategy_id in templates}


def _compact_history(history: dict) -> dict:
    """The exact manifest stays retained; cite its hash once beside ordered IDs."""
    manifest = json.dumps(history["provenance"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {**history, "features": {name: {key: value for key, value in feature.items() if key != "candle_ids"}
                                     for name, feature in history["features"].items()},
            "provenance": {
                "source": history["source"], "feature_snapshot_ref": history["source_ref"],
                "retained_candle_manifest_sha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
                "retained_candle_count": len(history["candle_ids"]),
                "availability_basis": "Retained source receipt times; publication times are unknown.",
            }}


def _unsupported_history(symbol: str, as_of: datetime, requested: list[str], source: str) -> dict:
    result = {
        "symbol": symbol, "as_of_utc": utc_iso(as_of), "source": source, "interval_minutes": 60,
        "feature_version": FEATURE_VERSION, "latest_close_time_utc": None,
        "latest_expected_close_time_utc": utc_iso(as_of.replace(minute=0, second=0, microsecond=0)),
        "stale": True, "candle_ids": [], "provenance": [], "values": {},
        "latest_close": None, "recent_candles": [], "recent_missing_close_times_utc": [],
        "features": {name: {
            "status": "unsupported", "definition": "Hourly history supports BTC/USD and ETH/USD only.",
            "lookback_candles": None, "available_candles": 0, "missing_close_times_utc": [],
            "window_start_close_time_utc": None, "window_end_close_time_utc": None,
            "event_time_utc": None, "available_at_utc": None,
        } for name in requested}, "event_time_utc": None, "available_at_utc": None,
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    result["source_ref"] = "hourly-features:" + hashlib.sha256(encoded.encode("ascii")).hexdigest()
    return result


def hourly_history(execution) -> HourlyHistory:
    """Where the hourly features read their hours (D220): the candle store when the installation wired one to the
    execution (``execution.hourly_history``), otherwise the financial database's ``hourly_candles``."""
    return getattr(execution, "hourly_history", None) or HistoryStore(execution.database)


def market_context(execution, guard: dict, templates: dict, *, as_of: datetime,
                   candle_wait: dict | None = None, extra_symbols: tuple[str, ...] = ()) -> dict:
    """Quotes and hourly features for the mandate's coins, plus ``extra_symbols`` (a wake's coins of the day)."""
    requested = list(dict.fromkeys(feature for item in active_templates(templates, guard).values()
                                   for feature in item["features"]))
    maximum_quote_age = min(guard["policy"]["maximum_quote_age_seconds"],
                            guard["mandate"]["max_quote_age_seconds"])
    history_store = hourly_history(execution)
    market = {}
    for symbol in dict.fromkeys([*guard["mandate"]["symbols"], *extra_symbols]):
        quote = execution.latest_observation(symbol, utc_iso(as_of), execution.venue)
        source = "synthetic" if quote and quote.source in {"synthetic", "scripted", "replay"} else "kraken_public_ohlc"
        supported = history_store.supports(symbol)
        history = (history_store.snapshot(symbol, as_of, requested, source=source) if supported
                   else _unsupported_history(symbol, as_of, requested, source))
        history = _compact_history(history)
        if supported:
            # A service fact beside the hashed features; the retained snapshot keeps it.
            history["expected_candle"] = expected_candle_status(history_store, symbol, as_of, candle_wait,
                                                                source=source)
        features = quote_features([quote]) if quote and quote.bid is not None and quote.ask is not None else {}
        age = (as_of - quote.event_time_utc).total_seconds() if quote else None
        market[symbol] = {
            "observation": quote.model_dump(mode="json") if quote else None,
            "features": {**features, **history["values"]},
            "history": history,
            "fresh": age is not None and 0 <= age <= maximum_quote_age,
        }
    return market
