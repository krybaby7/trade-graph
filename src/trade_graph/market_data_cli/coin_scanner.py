"""Coin scanner commands (T31). Discovered here like the candle commands, so no shared list changes.

``scan-coins`` makes one scan of every Kraken USD pair in the stored catalog and records it in the paper state
database: one public Kraken Ticker request for the bid and ask (none with ``--fixture-ticker``), no key, no model
and no paid call. ``--replay`` scans a past moment on stored history instead; a replay takes no ticker and is
always labelled optimistic. ``coin-shortlist`` only reads the newest recorded live scan (or replay).

Running the scan every hour is an installation step (the event engine or a scheduled task); this command is one
scan.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from trade_graph.contracts.coin_scanner import EXCLUSION_REASONS
from trade_graph.domain.clock import Clock, SystemClock
from trade_graph.domain.errors import TradeGraphError

SCAN = "scan-coins"
SHORTLIST = "coin-shortlist"
DEFAULT_DATABASE = "runtime/trade_graph.sqlite"
_REASON_WORDS = {
    "not_normal_status": "not in normal trading", "stablecoin": "stablecoin", "yield_token": "yield-paying token",
    "fiat_currency": "fiat currency", "held_for_review": "held until reviewed", "price_peg": "sits at one dollar",
    "price_pinned": "price barely moves", "peg_check_unavailable": "peg check impossible",
    "missing_candles": "no recent candles", "missing_volume": "gap in the last day of candles",
    "missing_swing": "too little history for the swing", "missing_trend": "too little history for the trend",
    "missing_spread": "no bid and ask", "thin_volume": "too little trading", "wide_spread": "spread too wide",
    "evaluation_failed": "could not be evaluated",
}


def build_clock() -> Clock:
    return SystemClock()


def build_ticker_transport():
    from trade_graph.adapters.market.kraken_ticker import make_ticker_transport

    return make_ticker_transport()


def register(sub: argparse._SubParsersAction) -> None:
    from trade_graph.market_data_cli._common import add_market_database_option

    scan = sub.add_parser(SCAN, help="rank every Kraken USD pair now and record the shortlist (no AI, no key)")
    scan.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    add_market_database_option(scan)
    scan.add_argument("--fixture-ticker", help="read bid and ask from a labelled fixture instead of Kraken")
    scan.add_argument("--replay", action="store_true", help="scan a past moment on stored history (optimistic)")
    scan.add_argument("--as-of", help="replay moment in UTC, for example 2026-09-30T23:05:00Z")
    scan.add_argument("--source", default="kraken_ohlcvt_archive", help="candle source a replay reads")
    report = sub.add_parser(SHORTLIST, help="read-only: the newest recorded coin shortlist")
    report.add_argument("--database", default=DEFAULT_DATABASE, help="the paper state database")
    report.add_argument("--replay", action="store_true", help="show the newest replay instead of the live list")
    report.add_argument("--format", choices=["text", "json"], default="text")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int | None:
    if args.command == SCAN:
        return _scan(args, parser)
    if args.command == SHORTLIST:
        return _shortlist(args, parser)
    return None


def _state_database(path: str, parser: argparse.ArgumentParser) -> Path:
    target = Path(path)
    if target.is_symlink() or target.parent.is_symlink() or not target.is_file():
        parser.error(f"no paper state database at {path}; initialise the paper account first")
    if target.parent.stat().st_mode & 0o077:
        parser.error("the paper state database requires a private directory with mode 0700")
    return target


def _market_database(path: str, parser: argparse.ArgumentParser) -> Path:
    target = Path(path)
    if target.is_symlink() or not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no candle store at {path}; collect or import candles first")
    return target


def _moment(value: str | None, parser: argparse.ArgumentParser) -> datetime:
    if value is None:
        parser.error("a replay needs --as-of, the UTC moment to scan")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parser.error(f"cannot read the time {value!r}; use for example 2026-09-30T23:05:00Z")
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        parser.error("--as-of must be UTC, ending in Z")
    return parsed


def _scan(args, parser) -> int:
    from trade_graph.adapters.market.kraken_ticker import KrakenTickerFetcher, TickerFetchError, load_ticker_fixture
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.coin_scanner.scanner import CoinScanner
    from trade_graph.application.coin_scanner.store import CoinScanStore
    from trade_graph.market_data_cli._common import open_store

    if args.replay and args.fixture_ticker:
        parser.error("a replay has no historical bid and ask; --fixture-ticker is for live scans only")
    as_of = _moment(args.as_of, parser) if args.replay else None
    state_path = _state_database(args.database, parser)
    market_path = _market_database(args.market_database, parser)
    clock = build_clock()
    ticker, ticker_error, requests = None, None, 0
    if not args.replay:
        if args.fixture_ticker:
            try:
                ticker = load_ticker_fixture(args.fixture_ticker)
            except TradeGraphError as error:
                parser.error(f"the ticker fixture was refused ({error}); nothing was recorded")
        else:
            requests = 1
            try:
                ticker = KrakenTickerFetcher(build_ticker_transport(), clock).fetch()
            except TickerFetchError as error:
                ticker_error = error.category
        as_of = clock.now()
    market = database = None
    try:
        market, candles = open_store(market_path)
        result = CoinScanner(candles, clock=clock).scan(
            as_of=as_of, ticker=ticker, mode="replay" if args.replay else "live",
            source=args.source if args.replay else None, ticker_error=ticker_error)
        database = Database(state_path)
        stored = CoinScanStore(database, clock).record(result)
    except (ValueError, OSError, sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"coin scan stopped ({type(error).__name__}); nothing was recorded")
    finally:
        if database is not None:
            database.close()
        if market is not None:
            market.close()
    document = stored.document
    print(json.dumps({
        "command": SCAN, "scan_id": stored.scan_id, "as_of_utc": document["scan"]["as_of_utc"],
        **{key: document["scan"][key] for key in ("mode", "status", "error", "evidence", "optimistic",
                                                   "optimism_reasons", "ranking_version")},
        "public_requests": requests, "model_calls": 0, "paid_calls": False, "ticker": document["ticker"],
        "counts": document["counts"], "excluded": document["excluded"],
        "shortlist": _shortlist_rows(stored)}))
    return 0


def _shortlist_rows(stored) -> list[dict[str, Any]]:
    places = {row["symbol"]: row for row in stored.document["ranking"]}
    return [{"position": entry["position"], "symbol": entry["symbol"], "score": entry["score"],
             "values": places[entry["symbol"]]["values"], "flags": entry["flags"]} for entry in stored.entries]


def _shortlist(args, parser) -> int:
    from trade_graph.application.coin_scanner.store import read_latest_scan

    target = Path(args.database)
    if not target.is_file() or target.stat().st_size == 0:
        parser.error(f"no state database at {args.database}; the report only reads one that already exists")
    try:
        connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        parser.error(f"cannot open {args.database} read-only")
    connection.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        stored = read_latest_scan(connection, mode="replay" if args.replay else "live") \
            if "coin_scans" in tables else None
    except (sqlite3.DatabaseError, TradeGraphError) as error:
        parser.error(f"cannot read the coin scans ({type(error).__name__})")
    finally:
        connection.close()
    kind = "replay" if args.replay else "live"
    if stored is None:
        message = f"No {kind} coin scan has been recorded yet."
        print(json.dumps({"command": SHORTLIST, "found": False, "message": message}) if args.format == "json"
              else message)
        return 0
    if args.format == "json":
        document = stored.document
        print(json.dumps({"command": SHORTLIST, "found": True, "scan_id": stored.scan_id,
                          "as_of_utc": document["scan"]["as_of_utc"], "mode": stored.mode, "status": stored.status,
                          "error": stored.error, "optimistic": stored.optimistic, "evidence": stored.evidence,
                          "ranking_version": stored.ranking_version, "counts": document["counts"],
                          "excluded": document["excluded"], "shortlist": _shortlist_rows(stored)}))
    else:
        print(format_shortlist_text(stored))
    return 0


def _percent(value: Decimal, places: str = "0.01") -> str:
    return f"{(value * 100).quantize(Decimal(places), rounding=ROUND_HALF_EVEN)}%"


def _usd(value: Decimal) -> str:
    if value >= 1_000_000:
        return f"${(value / 1_000_000).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}M"
    return f"${(value / 1_000).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}k"


def format_shortlist_text(stored) -> str:
    """Plain words for the owner. Display rounding only; the stored numbers stay exact."""
    document = stored.document
    scan = document["scan"]
    moment = scan["as_of_utc"][:16].replace("T", " ")
    labels = {"synthetic_fixture": "synthetic test data", "recorded_public_fixture": "recorded public data",
              "local_archive_file": "Kraken's downloaded history", "live_public_smoke": "a public smoke run",
              "live_public": "live public data"}
    lines = [f"Shortlist from the coin scan at {moment} UTC ({stored.mode}, {labels.get(stored.evidence)}; "
             f"ranking {stored.ranking_version}).",
             "Software made this list from prices and fees; no AI was used. It is a list of candidates for "
             "Research, not advice."]
    if stored.optimistic:
        lines.append("This is a replay, so it looks better than reality: "
                     + " ".join(reason["text"] for reason in scan["optimism_reasons"]))
    if stored.status == "failed":
        lines.append(f"The scan failed: {stored.error}. Nothing was ranked.")
    places = {row["symbol"]: row for row in document["ranking"]}
    trend_name = document["rules"]["trend"]["indicator"]
    for entry in stored.entries:
        values = places[entry["symbol"]]["values"]
        swing = Decimal(entry["inputs"]["swing"]["value"]) / 100
        parts = [f"traded {_usd(Decimal(values['volume_usd_24h']))} in 24 h",
                 f"typical hourly move {_percent(swing)} = "
                 f"{Decimal(values['swing_to_cost']).quantize(Decimal('0.1'))} x the round-trip fee"]
        if values["spread"] is not None:
            parts.append(f"spread {_percent(Decimal(values['spread']), '0.001')}")
        trend = Decimal(values["trend"])
        parts.append(f"24 h change {'+' if trend >= 0 else ''}{_percent(trend)}" if trend_name == "ROCP"
                     else f"trend ({trend_name}) {trend}")
        note = " (never individually reviewed)" if "not_individually_reviewed" in entry["flags"] else ""
        lines.append(f"{entry['position']:>2}. {entry['symbol']:<12} " + "; ".join(parts) + note)
    counts = document["counts"]
    lines.append(f"{counts['usd_pairs']} Kraken USD pairs were checked; {counts['ranked']} passed every check.")
    excluded = document["excluded"]
    if excluded:
        reasons = [reason for reason in EXCLUSION_REASONS if reason in excluded]
        lines.append("Why the others were not ranked (a pair can have several reasons): " + ", ".join(
            f"{_REASON_WORDS.get(reason, reason)} {len(excluded[reason])}" for reason in reasons) + ".")
    return "\n".join(lines)
